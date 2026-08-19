#!/usr/bin/env bun
import { parseCommand } from "./commands";
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
  const globals = parseGlobals(argv);
  let args = globals.args;
  if (args.length === 0) {
    if (process.stdin.isTTY && process.stdout.isTTY) args = ["ui"];
    else {
      process.stdout.write(HELP);
      return 0;
    }
  }
  try {
    const command = parseCommand(args);
    if (command.route === "local") {
      if (command.local === "help") process.stdout.write(HELP);
      if (command.local === "version") process.stdout.write(`OpenWhisper ${VERSION}\nprotocol 2 (compatible with 1)\n`);
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
    const client = await connectClient(globals.autoStart);
    try {
      if (command.route === "ui") {
        if (!process.stdin.isTTY || !process.stdout.isTTY) throw localError("usage", "The TUI requires an interactive terminal.");
        const { runTui } = await import("./tui");
        await runTui(client);
        return 0;
      }
      const result = await client.request(command.method ?? "", command.params);
      process.stdout.write(renderOutput(result, globals.output));
      return 0;
    } finally {
      client.close();
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
  for (const argument of argv) {
    if (argument === "--json") output = "json";
    else if (argument === "--jsonl") output = "jsonl";
    else if (argument === "--plain" || argument === "--no-color") output = "plain";
    else if (argument === "--no-start") autoStart = false;
    else args.push(argument);
  }
  return { args, output, autoStart };
}

function runtimeArgs(): string[] {
  const entry = process.argv[1];
  const sourceLaunch = entry === Bun.main || entry?.endsWith(".ts") || entry?.endsWith(".js");
  return process.argv.slice(sourceLaunch ? 2 : 1);
}

if (import.meta.main) process.exitCode = await run(runtimeArgs());
