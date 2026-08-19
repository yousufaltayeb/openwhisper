import type {
  BootstrapDto,
  EngineAdapter,
  EngineEvent,
  EngineEventListener,
} from "../engine/types";

export const bootstrapFixture: BootstrapDto = {
  protocolVersion: 1,
  engineSessionId: "test-engine",
  firstRun: false,
  settings: {
    transcriptionProvider: "faster-whisper",
    transcriptionModel: "large-v3-turbo",
    device: "auto",
    language: "auto",
    cleanupMode: "raw",
    cleanupProvider: "none",
    customCleanupPrompt: "",
    shortcutMode: "toggle",
    shortcut: "<alt>+o",
    liveInsertion: false,
    retentionDays: 30,
    notifications: false,
    activeModeId: "raw",
    onboardingCompleted: true,
    theme: "system",
    reducedMotion: false,
    retainAudio: false,
    audioRetentionDays: 7,
    audioDeviceId: null,
  },
  providers: [
    {
      id: "faster-whisper",
      name: "Faster Whisper",
      description: "Local, private transcription.",
      models: ["large-v3-turbo"],
      supportsStreaming: true,
      needsApiKey: false,
      available: true,
      unavailableReason: null,
      supportsTranscription: true,
      supportsCleanup: false,
    },
  ],
  dictation: { state: "idle", sessionId: null },
  availableMethods: ["app.bootstrap", "dictation.start", "settings.update"],
};

export class FakeEngineAdapter implements EngineAdapter {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  private listener: EngineEventListener | null = null;

  constructor(
    private bootstrap = structuredClone(bootstrapFixture),
    private restartFailures = 0,
  ) {}

  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    this.calls.push({ method, params });
    if (method === "app.bootstrap") {
      return structuredClone(this.bootstrap) as T;
    }
    if (method === "settings.update") {
      const changes = params.changes;
      if (changes && typeof changes === "object") {
        this.bootstrap.settings = { ...this.bootstrap.settings, ...changes };
      }
      return { accepted: true } as T;
    }
    if (method === "app.restartEngine") {
      if (this.restartFailures > 0) {
        this.restartFailures -= 1;
        throw new Error("The test engine restart failed.");
      }
      return structuredClone(this.bootstrap) as T;
    }
    if (method === "audio.testDevice") {
      return { ready: true, message: "The test microphone opened successfully." } as T;
    }
    if (method === "diagnostics.run") {
      return { "Global shortcut": "ready — Shortcut permission is active." } as T;
    }
    return { accepted: true } as T;
  }

  async listen(listener: EngineEventListener): Promise<() => void> {
    this.listener = listener;
    return () => {
      this.listener = null;
    };
  }

  emit(event: EngineEvent) {
    this.listener?.(event);
  }
}

export function event(
  seq: number,
  name: EngineEvent["event"],
  payload: Record<string, unknown>,
): EngineEvent {
  return { v: 1, kind: "event", seq, event: name, payload };
}
