#!/usr/bin/env bun
import { parseCommand } from "./commands";
import { mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import type { JsonValue } from "./protocol.generated";
import { OpenWhisperError, localError } from "./errors";
import { completion, HELP } from "./help";
import { renderOutput, type OutputMode } from "./output";
import { connectClient, startDaemon } from "./service";

const VERSION = "1.0.0-alpha.1";

interface GlobalOptions {
  args: string[];
  output: OutputMode;
  autoStart: boolean;
}

export async function run(argv = process.argv.slice(2)): Promise<number> {
  try {
    const globals = parseGlobals(argv);
    let args = globals.args;
    if (args.length === 0) {
      if (process.stdin.isTTY && process.stdout.isTTY) args = ["ui"];
      else {
        process.stdout.write(HELP);
        return 0;
      }
    }
    const command = parseCommand(args);
    if (command.route === "local") {
      if (command.local === "help") process.stdout.write(HELP);
      if (command.local === "version") process.stdout.write(`OpenWhisper ${VERSION}\nprotocol 3 (compatible with 2)\n`);
      if (command.local === "completion") {
        const shell = command.params && typeof command.params === "object" && !Array.isArray(command.params) ? String(command.params.shell) : "bash";
        process.stdout.write(completion(shell));
      }
      if (command.local === "service_start") {
        await startDaemon();
        process.stdout.write(renderOutput({ running: true }, globals.output));
      }
      return 0;
    }
    const client = await connectClient(command.forceNoStart ? false : globals.autoStart);
    let stdinStage: string | undefined;
    try {
      if (command.route === "ui") {
        if (!process.stdin.isTTY || !process.stdout.isTTY) throw localError("usage", "The TUI requires an interactive terminal.");
        const { runTui } = await import("./tui");
        await runTui(client);
        return 0;
      }
      if (command.method === "transcribe.file" && command.params && typeof command.params === "object" && !Array.isArray(command.params) && command.params.path === "-") {
        stdinStage = await stageStdin();
        command.params.path = stdinStage;
      }
      if (command.method === "models.install" && command.params && typeof command.params === "object" && !Array.isArray(command.params) && command.params.yes !== true) {
        if (!process.stdin.isTTY) throw localError("usage", "Model installation requires --yes when stdin is not a TTY.");
        const catalog = await client.request("models.list");
        const name = String(command.params.name ?? "balanced");
        const item = Array.isArray(catalog) ? catalog.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && candidate.name === name) : undefined;
        if (item && typeof item === "object" && !Array.isArray(item)) {
          process.stderr.write(`Model: ${name} (${String(item.model_id ?? "unknown")})\nSource: ${String(item.source ?? "unpublished")}\nTrust: ${String(item.trust ?? "unverified")}\nLicense: ${String(item.license ?? "unverified")}\nSize: ${String(item.size_bytes ?? "unpublished")} bytes\nSHA-256: ${String(item.sha256 ?? "unpublished")}\nWorker ABI: ${String(item.worker_abi ?? "unpublished")}\nBenchmark: ${String(item.benchmark_status ?? "unpublished")} (non-blocking)\n`);
        }
        const answer = prompt("Install this model? [y/N] ");
        if (!answer || !["y", "yes"].includes(answer.trim().toLowerCase())) throw localError("cancelled", "Model installation cancelled.");
        command.params.yes = true;
      }
      let progressWritten = false;
      let lastProgressPercent = -1;
      const unsubscribeProgress = command.method === "models.install"
        ? client.subscribe(0, (message) => {
          if (message.type !== "event" || message.event !== "model.download.progress") return;
          const data = message.data;
          if (!data || typeof data !== "object" || Array.isArray(data)) return;
          const downloaded = Number(data.downloaded_bytes ?? 0);
          const total = Number(data.total_bytes ?? 0);
          const percent = total > 0 ? Math.min(100, Math.floor(downloaded * 100 / total)) : 0;
          if (!process.stderr.isTTY && percent === lastProgressPercent) return;
          lastProgressPercent = percent;
          const line = `Downloading ${String(data.name ?? "balanced")}: ${downloaded}/${total} bytes (${percent}%)`;
          process.stderr.write(process.stderr.isTTY ? `\r${line}` : `${line}\n`);
          progressWritten = true;
        })
        : undefined;
      let result: JsonValue;
      try {
        result = await client.request(command.method ?? "", command.params);
      } finally {
        unsubscribeProgress?.();
        if (progressWritten && process.stderr.isTTY) process.stderr.write("\n");
      }
      const plainTranscript = globals.output === "plain" && (command.method === "transcribe.file" || (command.method === "record.stop"
        && command.params && typeof command.params === "object" && !Array.isArray(command.params) && command.params.wait === true))
        && result && typeof result === "object" && !Array.isArray(result) && typeof result.final_text === "string";
      const outputValue: JsonValue = plainTranscript ? String((result as Record<string, JsonValue>).final_text) : result;
      process.stdout.write(renderOutput(outputValue, globals.output));
      return 0;
    } finally {
      client.close();
      if (stdinStage) await rm(dirname(stdinStage), { recursive: true, force: true });
    }
  } catch (error) {
    if (error instanceof OpenWhisperError) {
      process.stderr.write(`openwhisper: ${error.message}\n`);
      if (error.action) process.stderr.write(`${error.action}\n`);
      return error.exitCode;
    }
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`openwhisper: ${message}\n`);
    return 6;
  }
}

function parseGlobals(argv: string[]): GlobalOptions {
  let output: OutputMode = process.env.NO_COLOR ? "plain" : "human";
  let autoStart = true;
  const args: string[] = [];
  let selectedOutput: string | undefined;
  for (const argument of argv) {
    const candidate = argument === "--json" ? "json" : argument === "--jsonl" ? "jsonl" : argument === "--plain" ? "plain" : undefined;
    if (candidate) {
      if (selectedOutput && selectedOutput !== candidate) throw localError("usage", "Choose only one of --plain, --json, or --jsonl.");
      selectedOutput = candidate;
      output = candidate as OutputMode;
    }
    else if (argument === "--no-color") { if (output === "human") output = "plain"; }
    else if (argument === "--no-start") autoStart = false;
    else args.push(argument);
  }
  return { args, output, autoStart };
}

async function stageStdin(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), "openwhisper-stdin-"));
  const path = join(directory, "input.wav");
  const file = await open(path, "wx", 0o600);
  try {
    for await (const chunk of process.stdin) await file.write(chunk as Buffer);
    await file.sync();
  } finally {
    await file.close();
  }
  return path;
}

function runtimeArgs(): string[] {
  const entry = process.argv[1];
  const sourceLaunch = entry === Bun.main || entry?.endsWith(".ts") || entry?.endsWith(".js");
  return process.argv.slice(sourceLaunch ? 2 : 1);
}

if (import.meta.main) process.exitCode = await run(runtimeArgs());
