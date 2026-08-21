import { describe, expect, test } from "bun:test";

import {
  SETTINGS,
  cycleSetting,
  displaySettingValue,
  parseEditorValue,
  renderSettings,
  settingValue,
} from "../src/settings";

const config = {
  language: "auto",
  audio: { device: "", max_recording_seconds: 300 },
  model: { threads: 0 },
  privacy: { local_only: true },
};

describe("TUI settings model", () => {
  test("reads and labels nested configuration without exposing raw structure", () => {
    const device = SETTINGS.find((setting) => setting.key === "audio.device")!;
    const threads = SETTINGS.find((setting) => setting.key === "model.threads")!;
    expect(settingValue(config, "privacy.local_only")).toBe(true);
    expect(displaySettingValue(device, settingValue(config, device.key))).toBe("System default");
    expect(displaySettingValue(threads, settingValue(config, threads.key))).toBe("Automatic (0)");
    expect(renderSettings(config, 0, undefined, 100)).toContain("> Language");
    expect(renderSettings(config, 0, undefined, 100)).toContain("Language · Automatic handles");
    expect(renderSettings(config, 0, undefined, 52, true)).toBe("[1/16] Language: Automatic");
  });

  test("cycles constrained settings and validates typed values", () => {
    const language = SETTINGS.find((setting) => setting.key === "language")!;
    const maximum = SETTINGS.find((setting) => setting.key === "audio.max_recording_seconds")!;
    const device = SETTINGS.find((setting) => setting.key === "audio.device")!;
    expect(cycleSetting(language, "auto", 1)).toBe("en");
    expect(cycleSetting(language, "auto", -1)).toBe("ar");
    expect(parseEditorValue(maximum, "120")).toBe(120);
    expect(() => parseEditorValue(maximum, "9")).toThrow("between 10 and 600");
    expect(parseEditorValue(device, " 61 ")).toBe("61");
    expect(() => parseEditorValue(device, "bad\ndevice")).toThrow("Control characters");
  });
});
