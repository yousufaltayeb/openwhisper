import { afterEach, describe, expect, test } from "bun:test";
import { createTestRenderer, type TestRenderer } from "@opentui/core/testing";

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
    expect(frame).toContain("1 [Capt]");
    expect(frame).toContain("[R] stop");
    expect(frame).toContain("[C] cancel");
    expect(frame).toContain("[Q] quit");
    expect(frame).toContain("3 Modes");
    expect(frame).toContain("5 Modl");
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
});

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
