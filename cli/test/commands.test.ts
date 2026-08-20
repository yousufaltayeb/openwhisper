import { describe, expect, test } from "bun:test";

import { parseCommand } from "../src/commands";
import { OpenWhisperError } from "../src/errors";
import { requestTimeoutFor, resolveEndpoint, runtimePaths } from "../src/ipc";
import { renderOutput } from "../src/output";

describe("stable command contract", () => {
  test.each([
    [["record", "status"], "record.status"],
    [["history", "list"], "history.list"],
    [["modes", "list"], "modes.list"],
    [["vocab", "list"], "vocab.list"],
    [["snippets", "list"], "snippets.list"],
    [["models", "list"], "models.list"],
    [["providers", "list"], "providers.list"],
    [["config", "list"], "config.list"],
    [["doctor"], "system.doctor"],
  ])("maps %p to %s", (args, method) => {
    expect(parseCommand(args).method).toBe(method);
  });

  test("rejects missing values as usage errors", () => {
    expect(() => parseCommand(["config", "set", "mode"])).toThrow(OpenWhisperError);
    try {
      parseCommand(["config", "set", "mode"]);
    } catch (error) {
      expect((error as OpenWhisperError).exitCode).toBe(2);
    }
  });

  test("uses an isolated v1 runtime endpoint", () => {
    expect(resolveEndpoint({ OPENWHISPER_V1_HOME: "/tmp/ow" })).toEndWith("/tmp/ow/run/openwhisperd.sock");
    expect(resolveEndpoint({ XDG_RUNTIME_DIR: "/run/user/1000" })).toBe("/run/user/1000/openwhisper-v1/openwhisperd.sock");
  });

  test("rejects unknown flags, extra arguments, invalid enums and invalid limits", () => {
    for (const args of [
      ["doctor", "extra"], ["record", "start", "--wait"], ["record", "status", "--mode", "raw"],
      ["transcribe", "-", "--mode", "formal"], ["history", "list", "--limit", "0"],
      ["history", "list", "--wat"], ["config", "list", "extra"],
    ]) expect(() => parseCommand(args)).toThrow(OpenWhisperError);
  });

  test("uses no-start semantics for service status and stop", () => {
    expect(parseCommand(["service", "status"]).forceNoStart).toBeTrue();
    expect(parseCommand(["service", "stop"]).forceNoStart).toBeTrue();
  });

  test("parses transcription copy and model import paths strictly", async () => {
    const path = `/tmp/openwhisper-model-${crypto.randomUUID()}`;
    await Bun.write(path, "fixture");
    try {
      expect(parseCommand(["models", "import", "balanced", path]).params).toEqual({ name: "balanced", path });
      expect(parseCommand(["transcribe", "-", "--copy", "--language", "ar"]).params).toEqual({
        path: "-", mode: "raw", language: "ar", copy: true, insert: false, source: "stdin",
      });
    } finally { await Bun.file(path).delete(); }
  });

  test("centralizes all TypeScript runtime paths", () => {
    expect(runtimePaths({ XDG_CONFIG_HOME: "/c", XDG_DATA_HOME: "/d", XDG_CACHE_HOME: "/k", XDG_RUNTIME_DIR: "/r" }, "/home/test")).toEqual({
      config: "/c/openwhisper/v1", data: "/d/openwhisper/v1", cache: "/k/openwhisper/v1",
      runtime: "/r/openwhisper-v1", socket: "/r/openwhisper-v1/openwhisperd.sock",
    });
  });

  test("allows one hour only for explicit model installation", () => {
    expect(requestTimeoutFor("models.install")).toBe(3_600_000);
    expect(requestTimeoutFor("models.verify")).toBe(310_000);
    expect(requestTimeoutFor("transcribe.file")).toBe(310_000);
  });
});

describe("machine-readable output", () => {
  test("jsonl emits one array item per line", () => {
    expect(renderOutput([{ text: "شغّل cargo test" }, { text: "done" }], "jsonl")).toBe(
      '{"text":"شغّل cargo test"}\n{"text":"done"}\n',
    );
  });

  test("plain mixed-direction text is byte accurate", () => {
    const text = "شغّل cargo test من فضلك";
    expect(Buffer.from(renderOutput(text, "plain").trim())).toEqual(Buffer.from(text));
  });
});
