import { describe, expect, test } from "bun:test";

import { parseCommand } from "../src/commands";
import { OpenWhisperError } from "../src/errors";
import { resolveEndpoint } from "../src/ipc";
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
