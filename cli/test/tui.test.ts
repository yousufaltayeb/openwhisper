import { afterEach, describe, expect, test } from "bun:test";
import { createTestRenderer, type TestRenderer } from "@opentui/core/testing";

import type { JsonValue } from "../src/protocol.generated";
import { buildTuiScene } from "../src/tui";

let renderer: TestRenderer | undefined;

afterEach(() => renderer?.destroy());

describe("OpenWhisper operations board", () => {
  test("renders capture truth and logical Arabic in a normal terminal", async () => {
    const setup = await createTestRenderer({ width: 110, height: 30 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async () => null);
    renderer.root.add(scene.root);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("OpenWhisper");
    expect(frame).toContain("Ready for dictation");
    expect(frame).toContain("شغّل cargo test من فضلك");
    expect(frame).toContain("[R] record");
  });

  test("keeps primary state and navigation in a small terminal", async () => {
    const setup = await createTestRenderer({ width: 52, height: 16 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("capturing"), async () => null);
    renderer.root.add(scene.root);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("[REC]");
    expect(frame).toContain("Recording is active");
    expect(frame).toContain("dBFS · OPEN");
    expect(frame).toContain("1 [Capt]");
    expect(frame).toContain("[R] stop");
    expect(frame).toContain("[C] cancel");
    expect(frame).toContain("[Q] quit");
    expect(frame).toContain("3 Modes");
    expect(frame).toContain("5 Modl");
  });

  test("shows measured microphone flow instead of a decorative recording animation", async () => {
    const setup = await createTestRenderer({ width: 90, height: 24 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("capturing"), async () => null);
    renderer.root.add(scene.root);
    scene.updateAudioLevel({ dbfs: -18.4, peak_dbfs: -12.1, signal: true, clipping: false, bytes_captured: 65_536 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("-18.4 dBFS");
    expect(frame).toContain("Signal detected");
    expect(frame).toContain("64.0 KiB captured");
    expect(frame).toContain("████");
  });

  test("distinguishes an active silent stream from a missing microphone stream", async () => {
    const setup = await createTestRenderer({ width: 90, height: 24 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("capturing"), async () => null);
    renderer.root.add(scene.root);
    scene.updateAudioLevel({ dbfs: -60, peak_dbfs: -60, signal: false, clipping: false, bytes_captured: 32_768 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("Stream active · waiting for speech");
  });

  test("shows provisional, committed, backend, latency, and insertion state while speaking", async () => {
    const setup = await createTestRenderer({ width: 110, height: 30 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, {
      ...status("capturing"),
      actual_backend: "vulkan",
      streaming: { backend: { actual: "vulkan" }, insertion_status: "active" },
    }, async () => null);
    renderer.root.add(scene.root);
    scene.updateTranscriptionCommit({ committed: "شغّل cargo" });
    scene.updateTranscriptionPreview({ text: "test من فضلك", latency_ms: 184 });
    scene.updateInsertionState({ status: "active", inserted_bytes: 12 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Committed  شغّل cargo");
    expect(frame).toContain("Preview    test من فضلك");
    expect(frame).toContain("Backend: vulkan");
    expect(frame).toContain("Latency: 184 ms");
    expect(frame).toContain("Insertion: active");
  });

  test("number keys expose the diagnostic view", async () => {
    const setup = await createTestRenderer({ width: 90, height: 24 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async (method) =>
      method === "system.doctor" ? { audio: { available: false, fallback: "file/stdin" } } : null,
    );
    renderer.root.add(scene.root);
    setup.mockInput.pressKey("7");
    await Bun.sleep(0);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("Capability diagnostics");
  });

  test("loads daemon-backed history into the work stage", async () => {
    const setup = await createTestRenderer({ width: 90, height: 24 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async (method) =>
      method === "history.list" ? [{ id: "h1", text: "راجع pull request", created_at: "now" }] : null,
    );
    renderer.root.add(scene.root);
    setup.mockInput.pressKey("2");
    await Bun.sleep(0);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("راجع pull request");

    scene.updateStatus({ ...status("idle"), mode: "clean" });
    await Bun.sleep(0);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("راجع pull request");
  });

  test.each([
    ["transcribing", "[WORKING]"],
    ["processing", "[WORKING]"],
    ["delivering", "[WORKING]"],
    ["failed", "[DEGRADED]"],
  ])("names the %s capture phase", async (phase, marker) => {
    const setup = await createTestRenderer({ width: 90, height: 22 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status(phase), async () => null);
    renderer.root.add(scene.root);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain(marker);
  });

  test("shows the failed capture reason and honest clipboard/history outcome", async () => {
    const setup = await createTestRenderer({ width: 90, height: 24 });
    renderer = setup.renderer;
    const failed = {
      ...status("failed"),
      capture: { phase: "failed", message: "transcription returned no text" },
    };
    const scene = buildTuiScene(renderer, failed, async () => null);
    renderer.root.add(scene.root);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Capture failed");
    expect(frame).toContain("transcription returned no text");
    expect(frame).toContain("meter reaches SIGNAL");
    expect(frame).toContain("Clipboard: unchanged");
    expect(frame).not.toContain("Ready for dictation");
  });

  test("renders one structured Doctor row per capability", async () => {
    const setup = await createTestRenderer({ width: 100, height: 28 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async (method) => method === "system.doctor" ? {
      capabilities: {
        audio: { available: true, backend: "pw-record", detail: "16 kHz capture ready" },
        insertion: { available: false, backend: "clipboard", detail: "typing is unavailable", fallback: "Copy the displayed text." },
      },
    } : null);
    renderer.root.add(scene.root);
    setup.mockInput.pressKey("7");
    await Bun.sleep(10); await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("READY · pw-record");
    for (let index = 0; index < 10; index += 1) setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    const scrolled = setup.captureCharFrame();
    expect(scrolled).toContain("BLOCKED · clipboard");
    expect(scrolled).toContain("Recovery: Copy the displayed text.");
  });

  test("recovers a final logical-order transcript with delivery details", async () => {
    const setup = await createTestRenderer({ width: 100, height: 26 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async () => null);
    renderer.root.add(scene.root);
    scene.showResult({ final_text: "شغّل cargo test من فضلك", copied: false, history_id: "h1", duration_ms: 1200 });
    setup.resize(72, 22);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("شغّل cargo test من فضلك");
    for (let index = 0; index < 5; index += 1) setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    const details = setup.captureCharFrame();
    expect(details).toContain("not copied");
    expect(details).toContain("h1");
  });

  test("shows model progress and disables capture/install actions while installing", async () => {
    const setup = await createTestRenderer({ width: 100, height: 28 });
    renderer = setup.renderer;
    const scene = buildTuiScene(renderer, status("idle"), async () => null);
    renderer.root.add(scene.root);
    scene.selectView("Models");
    scene.updateModelProgress({ name: "balanced", downloaded_bytes: 287_020_598, total_bytes: 574_041_195 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("[WORKING]");
    expect(frame).toContain("Installing balanced");
    expect(frame).toContain("50%");
    expect(frame).toContain("record unavailable");
    expect(frame).toContain("model install in progress");
    expect(frame).toContain("Benchmark: not run");
  });

  test("selects model profiles and exposes verify/select/remove actions", async () => {
    const setup = await createTestRenderer({ width: 120, height: 34 });
    renderer = setup.renderer;
    const calls: Array<[string, JsonValue | undefined]> = [];
    const models = ["fast", "balanced", "accurate"].map((name, index) => ({
      name,
      model_id: name,
      artifact_name: `ggml-${name}.bin`,
      installed: index === 1,
      selected: index === 1,
      installing: false,
      trust: "builtin_pinned",
      benchmark_status: "not_run",
      verification_state: index === 1 ? "verified" : "missing",
      source: "https://example.invalid/model",
      license: "MIT",
      size_bytes: 10,
      sha256: "a".repeat(64),
      pinned_revision: "b".repeat(40),
      worker_abi: "openwhisper-worker-1",
    }));
    const current = status("idle");
    const scene = buildTuiScene(renderer, current, async (method, params) => {
      calls.push([method, params]);
      if (method === "models.list") return models;
      if (method === "providers.list") return [];
      if (method === "system.status") return current;
      return { name: "balanced" };
    });
    renderer.root.add(scene.root);
    setup.mockInput.pressKey("5");
    await Bun.sleep(10);
    setup.mockInput.pressArrow("down");
    setup.mockInput.pressKey("v");
    await Bun.sleep(10);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("> balanced · INSTALLED · SELECTED");
    expect(setup.captureCharFrame()).toContain("Benchmark not run — does not block dictation");
    expect(calls).toContainEqual(["models.verify", { name: "balanced" }]);
  });

  test("edits and persists configuration without leaving the TUI", async () => {
    const setup = await createTestRenderer({ width: 110, height: 34 });
    renderer = setup.renderer;
    const currentStatus = status("idle");
    const currentConfig: Record<string, JsonValue> = {
      mode: "raw",
      language: "auto",
      overlay: "auto",
      sounds: { start: true, stop: true },
      notifications: true,
      history: { enabled: true, retention_days: 30 },
      privacy: { local_only: true },
      audio: { backend: "auto", device: "", max_recording_seconds: 300 },
      model: { backend: "auto", threads: 0 },
      delivery: { clipboard: true, live_insert: false },
    };
    const writes: Array<Record<string, unknown>> = [];
    const scene = buildTuiScene(renderer, currentStatus, async (method, params) => {
      if (method === "config.list") return currentConfig;
      if (method === "system.status") return currentStatus;
      if (method === "config.set") {
        const request = params as { key: string; value: JsonValue };
        writes.push(request);
        setNested(currentConfig, String(request.key), request.value);
        if (request.key === "language") currentStatus.language = String(request.value);
        return request;
      }
      return null;
    });
    renderer.root.add(scene.root);

    setup.mockInput.pressKey("6");
    await Bun.sleep(10);
    await setup.renderOnce();
    let frame = setup.captureCharFrame();
    expect(frame).toContain("Settings · changes save immediately");
    expect(frame).toContain("> Language");
    expect(frame).toContain("Automatic");
    expect(frame).toContain("[←→] change");

    setup.mockInput.pressEnter();
    await Bun.sleep(10);
    await setup.renderOnce();
    expect(writes.at(-1)).toEqual({ key: "language", value: "en" });
    expect(setup.captureCharFrame()).toContain("language en");

    for (let index = 0; index < 3; index += 1) setup.mockInput.pressArrow("down");
    setup.mockInput.pressEnter();
    await setup.mockInput.typeText("61");
    await setup.renderOnce();
    frame = setup.captureCharFrame();
    expect(frame).toContain("[61_]");
    expect(frame).toContain("[Enter] save");
    expect(frame).toContain("[Esc] cancel");

    setup.mockInput.pressEnter();
    await Bun.sleep(10);
    await setup.renderOnce();
    expect(writes.at(-1)).toEqual({ key: "audio.device", value: "61" });
    expect(setup.captureCharFrame()).toContain("Microphone device");
    expect(setup.captureCharFrame()).toContain("61");
    expect(setup.captureCharFrame()).toContain("saved");
  });
});

function setNested(root: Record<string, JsonValue>, key: string, value: JsonValue) {
  const segments = key.split(".");
  let current = root;
  for (const segment of segments.slice(0, -1)) current = current[segment] as Record<string, JsonValue>;
  current[segments.at(-1)!] = value;
}

function status(phase: string) {
  return {
    daemon: "running",
    version: "1.0.0-alpha.1",
    capture: { phase },
    mode: "raw",
    language: "auto",
    local_only: true,
  };
}
