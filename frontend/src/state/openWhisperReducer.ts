import type {
  BootstrapDto,
  DictationState,
  EngineEvent,
  ComputeCapabilitiesDto,
  ProviderDto,
  ProviderProgressStage,
  ModelDto,
  SettingsDto,
  ThemePreference,
} from "../engine/types";
import {
  normalizeComputeCapabilities,
  normalizeComputeTarget,
  normalizeModel,
  normalizeModels,
  normalizeOutputMode,
} from "../engine/types";

export interface NoticeState {
  level: "warning" | "error" | "info";
  message: string;
}

export interface OpenWhisperState {
  connection: "booting" | "ready" | "fatal";
  fatalMessage: string | null;
  settings: SettingsDto | null;
  providers: ProviderDto[];
  models: ModelDto[];
  compute: ComputeCapabilitiesDto | null;
  dictationState: DictationState;
  sessionId: string | null;
  lastEventSequence: number;
  transcript: string;
  finalTranscript: string;
  audioLevel: number;
  audioPeak: number;
  recordingElapsed: number;
  providerProgressStage: ProviderProgressStage | null;
  insertionResult: "waiting" | "inserted" | "copied" | "both" | "cancelled" | "failed";
  inserted: boolean;
  copied: boolean;
  notice: NoticeState | null;
  shortcutStatus: string | null;
  theme: ThemePreference;
  reducedMotion: boolean;
  commandDrawerOpen: boolean;
  onboardingOpen: boolean;
  pendingAction: string | null;
}

export const initialState: OpenWhisperState = {
  connection: "booting",
  fatalMessage: null,
  settings: null,
  providers: [],
  models: [],
  compute: null,
  dictationState: "idle",
  sessionId: null,
  lastEventSequence: 0,
  transcript: "",
  finalTranscript: "",
  audioLevel: 0,
  audioPeak: 0,
  recordingElapsed: 0,
  providerProgressStage: null,
  insertionResult: "waiting",
  inserted: false,
  copied: false,
  notice: null,
  shortcutStatus: null,
  theme: "system",
  reducedMotion: false,
  commandDrawerOpen: false,
  onboardingOpen: false,
  pendingAction: null,
};

export type OpenWhisperAction =
  | { type: "app.bootstrapped"; bootstrap: BootstrapDto }
  | { type: "app.failed"; message: string }
  | { type: "engine.event"; event: EngineEvent }
  | { type: "settings.updated"; settings: SettingsDto }
  | { type: "settings.models.loaded"; models: unknown }
  | { type: "settings.compute.loaded"; compute: unknown }
  | { type: "theme.changed"; theme: ThemePreference }
  | { type: "commands.open" }
  | { type: "commands.close" }
  | { type: "notice.added"; level: NoticeState["level"]; message: string }
  | { type: "notice.dismissed" }
  | { type: "onboarding.completed" }
  | { type: "request.started"; method: string }
  | { type: "request.finished"; method: string };

export function openWhisperReducer(
  state: OpenWhisperState,
  action: OpenWhisperAction,
): OpenWhisperState {
  switch (action.type) {
    case "app.bootstrapped":
      return {
        ...state,
        connection: "ready",
        settings: normalizeSettings(action.bootstrap.settings),
        providers: action.bootstrap.providers,
        models: normalizeModels(action.bootstrap.models),
        compute: normalizeComputeCapabilities(action.bootstrap.compute),
        dictationState: action.bootstrap.dictation.state,
        sessionId: action.bootstrap.dictation.sessionId,
        theme: action.bootstrap.settings.theme,
        reducedMotion: action.bootstrap.settings.reducedMotion,
        onboardingOpen: action.bootstrap.firstRun,
      };
    case "app.failed":
      return { ...state, connection: "fatal", fatalMessage: action.message };
    case "engine.event":
      return reduceEngineEvent(state, action.event);
    case "settings.updated":
      return { ...state, settings: normalizeSettings(action.settings) };
    case "settings.models.loaded":
      return { ...state, models: normalizeModels(action.models) };
    case "settings.compute.loaded":
      return { ...state, compute: normalizeComputeCapabilities(action.compute) };
    case "theme.changed":
      return { ...state, theme: action.theme };
    case "commands.open":
      return { ...state, commandDrawerOpen: true };
    case "commands.close":
      return { ...state, commandDrawerOpen: false };
    case "notice.added":
      return { ...state, notice: { level: action.level, message: action.message } };
    case "notice.dismissed":
      return { ...state, notice: null };
    case "onboarding.completed":
      return {
        ...state,
        onboardingOpen: false,
        settings: state.settings
          ? { ...state.settings, onboardingCompleted: true }
          : state.settings,
      };
    case "request.started":
      return { ...state, pendingAction: action.method };
    case "request.finished":
      return state.pendingAction === action.method ? { ...state, pendingAction: null } : state;
  }
}

function reduceEngineEvent(state: OpenWhisperState, event: EngineEvent): OpenWhisperState {
  if (event.seq <= state.lastEventSequence) {
    return state;
  }
  const next = { ...state, lastEventSequence: event.seq };
  const eventSession = stringValue(event.payload.sessionId);

  if (event.event.startsWith("dictation.") && state.sessionId && eventSession !== state.sessionId) {
    if (!(event.event === "dictation.state" && event.payload.state === "recording")) {
      return next;
    }
  }

  switch (event.event) {
    case "dictation.state": {
      const dictationState = stateValue(event.payload.state);
      const isNewSession = dictationState === "recording" && eventSession !== state.sessionId;
      const clearsProviderProgress = [
        "recording",
        "completed",
        "cancelled",
        "failed",
        "idle",
      ].includes(dictationState);
      return {
        ...next,
        dictationState,
        sessionId: eventSession,
        transcript: isNewSession ? "" : state.transcript,
        finalTranscript: isNewSession ? "" : state.finalTranscript,
        audioLevel: dictationState === "recording" ? state.audioLevel : 0,
        recordingElapsed: isNewSession ? 0 : state.recordingElapsed,
        providerProgressStage: clearsProviderProgress ? null : state.providerProgressStage,
        insertionResult:
          dictationState === "cancelled"
            ? "cancelled"
            : dictationState === "failed"
              ? "failed"
              : isNewSession
                ? "waiting"
                : state.insertionResult,
        inserted: isNewSession ? false : state.inserted,
        copied: isNewSession ? false : state.copied,
      };
    }
    case "dictation.partial":
      return { ...next, transcript: stringValue(event.payload.text) ?? "" };
    case "dictation.audioLevel":
      return {
        ...next,
        audioLevel: numberValue(event.payload.rms),
        audioPeak: numberValue(event.payload.peak),
        recordingElapsed: numberValue(event.payload.elapsed),
      };
    case "dictation.completed": {
      const text = stringValue(event.payload.text) ?? "";
      const inserted =
        typeof event.payload.inserted === "boolean"
          ? event.payload.inserted
          : stringValue(event.payload.insertionMethod) !== "clipboard";
      const copied =
        typeof event.payload.copied === "boolean"
          ? event.payload.copied
          : stringValue(event.payload.insertionMethod) === "clipboard";
      return {
        ...next,
        transcript: text,
        finalTranscript: text,
        providerProgressStage: null,
        insertionResult: inserted && copied ? "both" : inserted ? "inserted" : copied ? "copied" : "failed",
        inserted,
        copied,
      };
    }
    case "provider.progress":
      return {
        ...next,
        providerProgressStage: providerProgressStageValue(event.payload.stage),
      };
    case "models.progress": {
      const modelId =
        stringValue(event.payload.modelId) ??
        (event.payload.model && typeof event.payload.model === "object"
          ? stringValue((event.payload.model as Record<string, unknown>).id)
          : null);
      if (!modelId) return next;
      const incoming = normalizeModel(event.payload.model);
      const progress = numberValue(event.payload.progress);
      const status: ModelDto["status"] | undefined =
        event.payload.status === "error" ||
        event.payload.status === "cancelled" ||
        event.payload.status === "installed" ||
        event.payload.status === "downloading"
          ? event.payload.status
          : event.payload.state === "error" ||
              event.payload.state === "cancelled" ||
              event.payload.state === "installed" ||
              event.payload.state === "downloading"
            ? event.payload.state
          : undefined;
      const models = next.models.some((model) => model.id === modelId)
        ? next.models.map((model) =>
            model.id === modelId
              ? {
                  ...model,
                  ...(incoming ? { displayName: incoming.displayName } : {}),
                  progress: progress <= 1 ? progress * 100 : progress,
                  ...(status ? { status } : {}),
                  ...(typeof event.payload.installedSize === "number" ? { sizeBytes: event.payload.installedSize } : {}),
                  ...(typeof event.payload.jobId === "string" ? { jobId: event.payload.jobId } : {}),
                  error: stringValue(event.payload.error) ?? model.error,
                }
              : model,
          )
        : incoming
          ? [incoming]
          : next.models;
      return { ...next, models };
    }
    case "models.changed": {
      const listed = normalizeModels(event.payload.models);
      if (listed.length) return { ...next, models: listed };
      const modelId = stringValue(event.payload.modelId);
      if (!modelId) return next;
      const incoming = normalizeModel(event.payload);
      const models = next.models.some((model) => model.id === modelId)
        ? next.models.map((model) =>
            model.id === modelId
              ? {
                  ...model,
                  ...(incoming ? { status: incoming.status } : {}),
                  ...(typeof event.payload.installedSize === "number" ? { sizeBytes: event.payload.installedSize } : {}),
                  ...(typeof event.payload.error === "string" ? { error: event.payload.error } : {}),
                }
              : model,
          )
        : incoming
          ? [...next.models, incoming]
          : next.models;
      return { ...next, models };
    }
    case "compute.changed": {
      const compute = normalizeComputeCapabilities(event.payload.compute ?? event.payload);
      return compute ? { ...next, compute } : next;
    }
    case "shortcut.status":
      return {
        ...next,
        shortcutStatus:
          stringValue(event.payload.description) ??
          stringValue(event.payload.message) ??
          stringValue(event.payload.status),
      };
    case "notice":
      return {
        ...next,
        notice: {
          level: noticeLevel(event.payload.level),
          message: stringValue(event.payload.message) ?? "OpenWhisper needs attention.",
        },
      };
    case "engine.fatal":
      return {
        ...next,
        connection: "fatal",
        fatalMessage:
          stringValue(event.payload.message) ?? "The engine stopped during dictation.",
      };
    default:
      return next;
  }
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : 0;
}

function stateValue(value: unknown): DictationState {
  const states: DictationState[] = [
    "idle",
    "recording",
    "processing",
    "cleaning",
    "inserting",
    "completed",
    "cancelled",
    "failed",
  ];
  return typeof value === "string" && states.includes(value as DictationState)
    ? (value as DictationState)
    : "failed";
}

function noticeLevel(value: unknown): NoticeState["level"] {
  return value === "warning" || value === "error" ? value : "info";
}

function providerProgressStageValue(value: unknown): ProviderProgressStage | null {
  const stages: ProviderProgressStage[] = [
    "queued",
    "loading_model",
    "requesting",
    "transcribing",
    "cleaning",
    "completed",
  ];
  return typeof value === "string" && stages.includes(value as ProviderProgressStage)
    ? (value as ProviderProgressStage)
    : null;
}

function normalizeSettings(settings: SettingsDto): SettingsDto {
  return {
    ...settings,
    device: normalizeComputeTarget(settings.device),
    outputMode: normalizeOutputMode(settings.outputMode),
  };
}
