import type {
  BootstrapDto,
  DictationState,
  EngineAdapter,
  EngineEvent,
  EngineEventListener,
  SettingsDto,
} from "./types";
import { BUILT_IN_FASTER_WHISPER_MODELS, DEFAULT_COMPUTE_CAPABILITIES } from "./modelCatalog";

type Fixture =
  | "ready"
  | "recording"
  | "processing"
  | "completed"
  | "error"
  | "fatal"
  | "onboarding"
  | "unavailable"
  | "long";

const fixtureText = "مرحبا OpenWhisper — the microphone is ready for العربية and English.";

const defaultSettings: SettingsDto = {
  transcriptionProvider: "faster-whisper",
  transcriptionModel: "large-v3-turbo",
  device: "auto",
  language: "auto",
  cleanupMode: "raw",
  cleanupProvider: "none",
  customCleanupPrompt: "",
  shortcutMode: "toggle",
  shortcut: "Alt + O",
  liveInsertion: false,
  outputMode: "insert",
  retentionDays: 30,
  notifications: false,
  activeModeId: "raw",
  onboardingCompleted: true,
  theme: "system",
  reducedMotion: false,
  retainAudio: false,
  audioRetentionDays: 7,
  audioDeviceId: null,
};

export class BrowserEngineAdapter implements EngineAdapter {
  private readonly listeners = new Set<EngineEventListener>();
  private readonly fixture: Fixture;
  private settings = { ...defaultSettings };
  private sequence = 0;
  private sessionId: string | null = null;
  private state: DictationState = "idle";
  private completionTimer: number | undefined;
  private modelDownloadTimer: number | undefined;
  private models = structuredClone(BUILT_IN_FASTER_WHISPER_MODELS);

  constructor(fixture?: Fixture) {
    const requested = fixture ?? new URLSearchParams(window.location.search).get("fixture") ?? "ready";
    this.fixture = isFixture(requested) ? requested : "ready";
    this.state = fixtureState(this.fixture);
    this.sessionId = this.state === "idle" ? null : "fixture-session-01";
  }

  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    switch (method) {
      case "app.bootstrap":
        return this.bootstrap() as T;
      case "dictation.start":
        this.start();
        return { accepted: true } as T;
      case "dictation.stop":
        this.stop();
        return { accepted: true } as T;
      case "dictation.cancel":
        this.cancel();
        return { accepted: true } as T;
      case "settings.update": {
        const changes = params.changes;
        if (changes && typeof changes === "object") {
          this.settings = { ...this.settings, ...(changes as Partial<SettingsDto>) };
        }
        return { ...this.settings } as T;
      }
      case "models.list":
        return { models: structuredClone(this.models) } as T;
      case "models.download":
        this.downloadModel(params.modelId ?? params.model);
        return { accepted: true } as T;
      case "models.cancel":
        this.cancelModel(params.modelId ?? params.model);
        return { accepted: true } as T;
      case "models.remove":
        this.removeModel(params.modelId ?? params.model);
        return { accepted: true } as T;
      case "modes.select":
        if (typeof params.modeId === "string") this.settings = { ...this.settings, activeModeId: params.modeId };
        return { activeModeId: this.settings.activeModeId } as T;
      case "compute.list":
      case "compute.capabilities":
      case "compute.probe":
        return structuredClone(DEFAULT_COMPUTE_CAPABILITIES) as T;
      case "app.restartEngine":
        return this.bootstrap() as T;
      case "audio.testDevice":
        return { ready: true, message: "The default microphone opened successfully." } as T;
      case "diagnostics.run":
        return {
          "Global shortcut": "ready — The desktop shortcut portal is available.",
          "Local runtime": "ready — Faster Whisper and Qt Multimedia are installed.",
        } as T;
      default:
        throw new Error(`The browser fixture does not implement ${method}.`);
    }
  }

  async listen(listener: EngineEventListener): Promise<() => void> {
    this.listeners.add(listener);
    window.setTimeout(() => this.emitFixture(), 0);
    return () => this.listeners.delete(listener);
  }

  private bootstrap(): BootstrapDto {
    return {
      protocolVersion: 1,
      engineSessionId: "browser-fixture-engine",
      firstRun: this.fixture === "onboarding",
      settings: {
        ...this.settings,
        onboardingCompleted: this.fixture !== "onboarding",
        transcriptionModel:
          this.fixture === "long"
            ? "large-v3-turbo-experimental-very-long-mixed-identifier-مرحبا-測試"
            : this.settings.transcriptionModel,
      },
      providers: [
        {
          id: "faster-whisper",
          name: "Faster Whisper",
          description: "Local, private transcription.",
          models: ["large-v3-turbo"],
          supportsStreaming: true,
          needsApiKey: false,
          available: this.fixture !== "unavailable",
          unavailableReason:
            this.fixture === "unavailable"
              ? "The local model runtime is unavailable. Reinstall OpenWhisper."
              : null,
          supportsTranscription: true,
          supportsCleanup: false,
        },
      ],
      models: structuredClone(this.models),
      compute: structuredClone(DEFAULT_COMPUTE_CAPABILITIES),
      dictation: { state: this.state, sessionId: this.sessionId },
      availableMethods: [
        "app.bootstrap",
        "dictation.start",
        "dictation.stop",
        "dictation.cancel",
        "settings.update",
        "modes.select",
        "models.list",
        "models.download",
        "models.cancel",
        "models.remove",
        "compute.list",
        "compute.capabilities",
        "compute.probe",
        "app.restartEngine",
        "audio.testDevice",
        "diagnostics.run",
      ],
    };
  }

  private emitFixture() {
    if (this.fixture === "fatal") {
      this.emit("engine.fatal", { message: "The engine stopped during dictation." });
      return;
    }
    if (this.fixture === "error") {
      this.emit("notice", { level: "error", message: "The microphone could not be opened." });
      return;
    }
    if (this.state !== "idle") {
      this.emit("dictation.state", { state: this.state, sessionId: this.sessionId });
      this.emit("dictation.partial", { text: fixtureText, sessionId: this.sessionId });
      this.emit("dictation.audioLevel", { rms: 0.58, peak: 0.74, sessionId: this.sessionId });
      if (this.state === "completed") {
        this.emit("dictation.completed", {
          text: fixtureText,
          provider: "faster-whisper",
          sessionId: this.sessionId,
        });
      }
    }
  }

  private start() {
    if (this.state === "recording") {
      throw new Error("Dictation is already recording.");
    }
    this.sessionId = "fixture-session-active";
    this.state = "recording";
    this.emit("dictation.state", { state: "recording", sessionId: this.sessionId });
    this.emit("dictation.audioLevel", { rms: 0.46, peak: 0.67, sessionId: this.sessionId });
    this.emit("dictation.partial", { text: "مرحبا OpenWhisper", sessionId: this.sessionId });
  }

  private stop() {
    if (this.state !== "recording") {
      throw new Error("No dictation is recording.");
    }
    this.state = "processing";
    this.emit("dictation.state", { state: "processing", sessionId: this.sessionId });
    this.completionTimer = window.setTimeout(() => {
      this.state = "completed";
      this.emit("dictation.completed", {
        text: fixtureText,
        provider: "faster-whisper",
        sessionId: this.sessionId,
      });
      this.emit("dictation.state", { state: "completed", sessionId: this.sessionId });
    }, 450);
  }

  private cancel() {
    if (this.completionTimer !== undefined) {
      window.clearTimeout(this.completionTimer);
    }
    this.state = "cancelled";
    this.emit("dictation.state", { state: "cancelled", sessionId: this.sessionId });
  }

  private downloadModel(value: unknown) {
    const id = typeof value === "string" ? value : null;
    const model = this.models.find((item) => item.id === id);
    if (!model) throw new Error("The requested model is not allowlisted.");
    if (this.modelDownloadTimer !== undefined) throw new Error("Another model is downloading.");
    model.status = "downloading";
    model.progress = 18;
    model.error = null;
    this.emit("models.progress", { modelId: model.id, progress: model.progress, status: model.status });
    this.modelDownloadTimer = window.setTimeout(() => {
      this.modelDownloadTimer = undefined;
      model.status = "installed";
      model.progress = 100;
      this.emit("models.progress", { modelId: model.id, progress: 100, status: "installed" });
      this.emit("models.changed", { models: structuredClone(this.models) });
    }, 360);
  }

  private cancelModel(value: unknown) {
    const id = typeof value === "string" ? value : null;
    const model = this.models.find((item) => item.id === id);
    if (!model || model.status !== "downloading") throw new Error("The model is not downloading.");
    if (this.modelDownloadTimer !== undefined) window.clearTimeout(this.modelDownloadTimer);
    this.modelDownloadTimer = undefined;
    model.status = "cancelled";
    this.emit("models.progress", { modelId: model.id, progress: model.progress, status: "cancelled" });
  }

  private removeModel(value: unknown) {
    const id = typeof value === "string" ? value : null;
    const model = this.models.find((item) => item.id === id);
    if (!model || model.status !== "installed") throw new Error("The model is not installed.");
    if (id === this.settings.transcriptionModel) throw new Error("The active model cannot be removed.");
    model.status = "not-installed";
    model.progress = 0;
    this.emit("models.changed", { models: structuredClone(this.models) });
  }

  private emit(event: EngineEvent["event"], payload: Record<string, unknown>) {
    this.sequence += 1;
    const frame: EngineEvent = { v: 1, kind: "event", seq: this.sequence, event, payload };
    for (const listener of this.listeners) {
      listener(frame);
    }
  }
}

function isFixture(value: string): value is Fixture {
  return [
    "ready",
    "recording",
    "processing",
    "completed",
    "error",
    "fatal",
    "onboarding",
    "unavailable",
    "long",
  ].includes(value);
}

function fixtureState(fixture: Fixture): DictationState {
  if (fixture === "recording" || fixture === "processing" || fixture === "completed") {
    return fixture;
  }
  return "idle";
}
