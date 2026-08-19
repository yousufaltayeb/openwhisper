import { createTestRenderer } from "@opentui/core/testing";
import { buildTuiScene } from "../src/tui";

const cases = [
  { name: "tui-wide.txt", width: 110, height: 30, status: { daemon: "running", version: "1.0.0-alpha.1", capture: { phase: "idle" }, capture_available: false, mode: "raw", language: "auto", local_only: true } },
  { name: "tui-narrow-recording.txt", width: 52, height: 16, status: { daemon: "running", version: "1.0.0-alpha.1", capture: { phase: "capturing" }, capture_available: true, mode: "code", language: "auto", local_only: true } },
] as const;

for (const entry of cases) {
  const setup = await createTestRenderer({ width: entry.width, height: entry.height });
  const scene = buildTuiScene(setup.renderer, entry.status, async () => null);
  setup.renderer.root.add(scene.root);
  await setup.renderOnce();
  await Bun.write(new URL(`../../fixtures/golden/${entry.name}`, import.meta.url), `${setup.captureCharFrame()}\n`);
  setup.renderer.destroy();
}
