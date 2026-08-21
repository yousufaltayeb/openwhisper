#!/usr/bin/env bun

import { resolve } from "node:path";

type JsonRecord = Record<string, unknown>;

const binary = resolve(import.meta.dir, "../target/debug/openwhisper");
const notificationId = "74691";

async function main() {
  const status = await openwhisper(["record", "status", "--json"]);
  if (!status.ok) {
    notify("OpenWhisper unavailable", status.error, "critical", 6_000);
    return 1;
  }
  const phase = capturePhase(status.value);
  if (phase === "capturing") {
    notify("Transcribing…", "Flushing the remaining stable words locally.", "normal", 0);
    const result = await openwhisper(["record", "stop", "--wait", "--json"]);
    if (!result.ok) {
      notify("Transcription failed", result.error, "critical", 8_000);
      return 1;
    }
    const insertion = String(result.value.insertion_status ?? result.value.insertion_method ?? "not_requested");
    const body = insertion === "complete"
      ? "Stable text was inserted into the retained window; the final transcript is copied."
      : insertion === "suspended"
        ? "The target changed or insertion failed. The complete transcript is copied."
        : insertion === "partial"
          ? "Finalization failed after partial insertion. Preserved partial text is copied."
          : result.value.copied === true
            ? "Transcript copied to the clipboard."
            : "Transcript is available in OpenWhisper History.";
    notify("Dictation complete", body, insertion === "partial" ? "critical" : "normal", 5_000);
    return 0;
  }
  if (["transcribing", "processing", "delivering"].includes(phase)) {
    notify("OpenWhisper is still working", "The current recording is finalizing locally.", "normal", 4_000);
    return 0;
  }
  const result = await openwhisper(["record", "start", "--insert-live", "--json"]);
  if (!result.ok) {
    notify("Recording could not start", result.error, "critical", 8_000);
    return 1;
  }
  notify("● OPEN · 00:00", "Microphone open. Press Alt+O again to stop.", "normal", 0);
  return 0;
}

async function openwhisper(args: string[]): Promise<
  { ok: true; value: JsonRecord } | { ok: false; error: string }
> {
  try {
    const child = Bun.spawn([binary, ...args], {
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
      env: process.env,
    });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    if (exitCode !== 0) return { ok: false, error: cleanError(stderr) };
    const value = JSON.parse(stdout) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? { ok: true, value: value as JsonRecord }
      : { ok: false, error: "OpenWhisper returned an invalid response." };
  } catch (error) {
    return { ok: false, error: cleanError(error instanceof Error ? error.message : String(error)) };
  }
}

function capturePhase(status: JsonRecord): string {
  const capture = status.capture;
  return capture && typeof capture === "object" && !Array.isArray(capture)
    ? String((capture as JsonRecord).phase ?? "idle")
    : "idle";
}

function notify(title: string, body: string, urgency: "normal" | "critical", timeout: number) {
  const child = Bun.spawnSync([
    "/usr/bin/dunstify",
    "-a", "OpenWhisper",
    "-r", notificationId,
    "-u", urgency,
    "-t", String(timeout),
    "-h", "string:x-dunst-stack-tag:openwhisper",
    title,
    body,
  ], { stdin: "ignore", stdout: "ignore", stderr: "ignore", env: process.env });
  if (child.exitCode !== 0) process.stderr.write(`${title}: ${body}\n`);
}

function cleanError(value: string): string {
  const firstLine = value.trim().split("\n").find(Boolean) ?? "The action failed.";
  return firstLine.replace(/^openwhisper:\s*/i, "").slice(0, 180);
}

process.exit(await main());
