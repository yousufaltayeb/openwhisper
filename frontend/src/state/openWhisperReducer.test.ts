import { describe, expect, it } from "vitest";

import { bootstrapFixture, event } from "../test/fixtures";
import { initialState, openWhisperReducer } from "./openWhisperReducer";

describe("openWhisperReducer", () => {
  it("hydrates the saved theme without browser persistence", () => {
    const state = openWhisperReducer(initialState, {
      type: "app.bootstrapped",
      bootstrap: {
        ...bootstrapFixture,
        settings: { ...bootstrapFixture.settings, theme: "dark", reducedMotion: true },
      },
    });

    expect(state.connection).toBe("ready");
    expect(state.theme).toBe("dark");
    expect(state.reducedMotion).toBe(true);
  });

  it("rejects stale sequences and stale dictation sessions", () => {
    const started = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(3, "dictation.state", {
        state: "recording",
        sessionId: "session-new",
      }),
    });
    const partial = openWhisperReducer(started, {
      type: "engine.event",
      event: event(4, "dictation.partial", {
        text: "مرحبا hello",
        sessionId: "session-new",
      }),
    });
    const staleSequence = openWhisperReducer(partial, {
      type: "engine.event",
      event: event(2, "dictation.partial", {
        text: "wrong",
        sessionId: "session-new",
      }),
    });
    const staleSession = openWhisperReducer(staleSequence, {
      type: "engine.event",
      event: event(5, "dictation.partial", {
        text: "also wrong",
        sessionId: "session-old",
      }),
    });

    expect(staleSession.transcript).toBe("مرحبا hello");
    expect(staleSession.lastEventSequence).toBe(5);
  });

  it("normalizes fatal recovery state without transcript details", () => {
    const state = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(1, "engine.fatal", { message: "The engine stopped during dictation." }),
    });
    expect(state.connection).toBe("fatal");
    expect(state.fatalMessage).toMatch(/stopped/);
  });

  it("reports clipboard fallback without treating preserved text as inserted", () => {
    const recording = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(1, "dictation.state", { state: "recording", sessionId: "copy" }),
    });
    const completed = openWhisperReducer(recording, {
      type: "engine.event",
      event: event(2, "dictation.completed", {
        text: "مرحبا clipboard",
        inserted: false,
        insertionMethod: "clipboard",
        sessionId: "copy",
      }),
    });

    expect(completed.finalTranscript).toBe("مرحبا clipboard");
    expect(completed.insertionResult).toBe("copied");
  });

  it("keeps the latest shortcut portal description", () => {
    const state = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(1, "shortcut.status", {
        status: "active",
        description: "Alt+Space assigned by the compositor",
      }),
    });

    expect(state.shortcutStatus).toBe("Alt+Space assigned by the compositor");
  });

  it("tracks provider work and clears it at the terminal dictation state", () => {
    const loading = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(1, "provider.progress", { stage: "loading_model" }),
    });
    expect(loading.providerProgressStage).toBe("loading_model");

    const completed = openWhisperReducer(loading, {
      type: "engine.event",
      event: event(2, "dictation.state", { state: "completed", sessionId: null }),
    });
    expect(completed.providerProgressStage).toBeNull();
  });

  it("migrates legacy accelerator and output settings at bootstrap", () => {
    const state = openWhisperReducer(initialState, {
      type: "app.bootstrapped",
      bootstrap: {
        ...bootstrapFixture,
        settings: { ...bootstrapFixture.settings, device: "cuda", outputMode: undefined },
      },
    });

    expect(state.settings?.device).toBe("nvidia");
    expect(state.settings?.outputMode).toBe("insert");
  });

  it("keeps separate inserted and copied completion outcomes", () => {
    const state = openWhisperReducer(initialState, {
      type: "engine.event",
      event: event(1, "dictation.completed", {
        text: "مرحبا both",
        inserted: true,
        copied: true,
      }),
    });

    expect(state.inserted).toBe(true);
    expect(state.copied).toBe(true);
    expect(state.insertionResult).toBe("both");
  });
});
