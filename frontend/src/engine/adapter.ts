import { BrowserEngineAdapter } from "./browserAdapter";
import { TauriEngineAdapter } from "./tauriAdapter";
import type { EngineAdapter } from "./types";

export function createEngineAdapter(): EngineAdapter {
  return window.__TAURI_INTERNALS__ ? new TauriEngineAdapter() : new BrowserEngineAdapter();
}
