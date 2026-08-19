import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type { EngineAdapter, EngineEvent, EngineEventListener } from "./types";

interface EngineFailure {
  code?: string;
  message?: string;
}

export class TauriEngineAdapter implements EngineAdapter {
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    try {
      return await invoke<T>("engine_request", { method, params });
    } catch (failure) {
      const error = failure as EngineFailure;
      throw new Error(error.message ?? "The OpenWhisper engine could not complete the action.");
    }
  }

  async listen(listener: EngineEventListener): Promise<() => void> {
    return listen<EngineEvent>("engine-event", (event) => listener(event.payload));
  }
}
