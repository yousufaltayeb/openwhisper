export type ThemePreference = "system" | "light" | "dark";

/** The accelerator choices exposed by the Settings surface. */
export type ComputeTarget = "auto" | "cpu" | "nvidia" | "amd";

/** How the finalized transcript is delivered to the focused application. */
export type OutputMode = "insert" | "clipboard" | "both";

export type ModelGroup = "multilingual" | "english" | "distilled" | "legacy";
export type ModelInstallStatus =
  | "not-installed"
  | "downloading"
  | "installed"
  | "error"
  | "cancelled";

export type DictationState =
  | "idle"
  | "recording"
  | "processing"
  | "cleaning"
  | "inserting"
  | "completed"
  | "cancelled"
  | "failed";

export type ProviderProgressStage =
  | "queued"
  | "loading_model"
  | "requesting"
  | "transcribing"
  | "cleaning"
  | "completed";

export interface SettingsDto {
  transcriptionProvider: string;
  transcriptionModel: string;
  /** `cuda` is accepted at the boundary for migration, but never emitted by the UI. */
  device: ComputeTarget | string;
  language: string;
  cleanupMode: string;
  cleanupProvider: string;
  customCleanupPrompt: string;
  shortcutMode: "toggle" | "push-to-talk";
  shortcut: string;
  liveInsertion: boolean;
  /** Added in the settings milestone. Missing values migrate to `insert`. */
  outputMode?: OutputMode;
  retentionDays: number;
  notifications: boolean;
  activeModeId: string;
  onboardingCompleted: boolean;
  theme: ThemePreference;
  reducedMotion: boolean;
  retainAudio: boolean;
  audioRetentionDays: number;
  audioDeviceId: string | null;
}

export interface ModelDto {
  id: string;
  displayName: string;
  group: ModelGroup;
  languages: string[];
  /** Human-readable relative comparison, for example `fast` or `highest`. */
  relativeSpeed: string;
  relativeQuality: string;
  sizeBytes: number | null;
  status: ModelInstallStatus;
  progress: number;
  error: string | null;
  supportsLiveTyping: boolean;
  aliases: string[];
  jobId?: string | null;
  selected?: boolean;
  unknownLegacy?: boolean;
}

export interface ComputeOptionDto {
  target: ComputeTarget;
  available: boolean;
  backend: string | null;
  reason: string | null;
  supportedComputeTypes: string[];
}

export interface ComputeCapabilitiesDto {
  active: ComputeTarget;
  automaticBackend: ComputeTarget;
  options: ComputeOptionDto[];
}

export interface ProviderDto {
  id: string;
  name: string;
  description: string;
  models: string[];
  supportsStreaming: boolean;
  needsApiKey: boolean;
  available: boolean;
  unavailableReason: string | null;
  supportsTranscription: boolean;
  supportsCleanup: boolean;
}

export interface BootstrapDto {
  protocolVersion: 1;
  engineSessionId: string;
  firstRun: boolean;
  settings: SettingsDto;
  providers: ProviderDto[];
  models?: ModelDto[];
  compute?: ComputeCapabilitiesDto;
  dictation: {
    state: DictationState;
    sessionId: string | null;
  };
  availableMethods: string[];
}

export interface EngineEvent {
  v: 1;
  kind: "event";
  seq: number;
  event:
    | "dictation.state"
    | "dictation.partial"
    | "dictation.audioLevel"
    | "dictation.completed"
    | "history.changed"
    | "provider.progress"
    | "models.progress"
    | "models.changed"
    | "compute.changed"
    | "shortcut.status"
    | "notice"
    | "engine.fatal";
  payload: Record<string, unknown>;
}

export type EngineEventListener = (event: EngineEvent) => void;

export interface EngineAdapter {
  request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
  listen(listener: EngineEventListener): Promise<() => void>;
}

/**
 * Values stored by older OpenWhisper releases are deliberately normalized in
 * the frontend boundary. This keeps old settings visible while ensuring new
 * requests use the public protocol vocabulary.
 */
export function normalizeComputeTarget(value: unknown): ComputeTarget {
  if (value === "cuda" || value === "nvidia") return "nvidia";
  if (value === "amd" || value === "rocm") return "amd";
  if (value === "cpu") return "cpu";
  return "auto";
}

export function normalizeOutputMode(value: unknown): OutputMode {
  return value === "clipboard" || value === "both" ? value : "insert";
}

function modelGroup(value: unknown): ModelGroup {
  if (value === "english" || value === "english-only") return "english";
  if (value === "distilled") return "distilled";
  if (value === "legacy") return "legacy";
  return "multilingual";
}

function modelStatus(value: unknown, installed: unknown): ModelInstallStatus {
  if (
    value === "downloading" ||
    value === "installed" ||
    value === "error" ||
    value === "cancelled"
  ) {
    return value;
  }
  if (value === "not_installed") return "not-installed";
  return installed === true ? "installed" : "not-installed";
}

function finiteNumber(value: unknown, fallback: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : fallback;
}

/** Convert a wire model record into the sanitized model shape rendered by Settings. */
export function normalizeModel(value: unknown): ModelDto | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const id =
    typeof record.id === "string"
      ? record.id
      : typeof record.modelId === "string"
        ? record.modelId
        : typeof record.model === "string"
          ? record.model
          : "";
  if (!id) return null;
  const rawLanguages = record.languages;
  const languages = Array.isArray(rawLanguages)
    ? rawLanguages.filter((item): item is string => typeof item === "string")
    : typeof rawLanguages === "string"
      ? [rawLanguages]
    : typeof record.languageScope === "string"
      ? [record.languageScope]
      : ["Multilingual"];
  const aliases = Array.isArray(record.aliases)
    ? record.aliases.filter((item): item is string => typeof item === "string")
    : [];
  const progress = finiteNumber(record.progress, 0) ?? 0;
  return {
    id,
    displayName:
      (typeof record.displayName === "string" && record.displayName) ||
      (typeof record.name === "string" && record.name) ||
      id,
    group: modelGroup(record.group ?? record.family ?? record.category),
    languages,
    relativeSpeed:
      (typeof record.relativeSpeed === "string" && record.relativeSpeed) ||
      (typeof record.speed === "string" && record.speed) ||
      "varies",
    relativeQuality:
      (typeof record.relativeQuality === "string" && record.relativeQuality) ||
      (typeof record.quality === "string" && record.quality) ||
      "varies",
    sizeBytes: finiteNumber(record.sizeBytes ?? record.installedSize ?? record.size, null),
    status: modelStatus(record.status ?? record.state, record.installed ?? record.state === "installed"),
    progress: Math.min(100, progress <= 1 ? progress * 100 : progress),
    error: typeof record.error === "string" ? record.error : null,
    supportsLiveTyping: record.supportsLiveTyping !== false,
    aliases,
    jobId: typeof record.jobId === "string" ? record.jobId : null,
    selected: record.selected === true,
    unknownLegacy: record.unknownLegacy === true || modelGroup(record.group ?? record.family ?? record.category) === "legacy",
  };
}

export function normalizeModels(value: unknown): ModelDto[] {
  const raw: unknown[] = Array.isArray(value)
    ? value
    : value && typeof value === "object" && Array.isArray((value as Record<string, unknown>).models)
      ? ((value as Record<string, unknown>).models as unknown[])
      : [];
  return raw.map(normalizeModel).filter((item): item is ModelDto => item !== null);
}

export function normalizeComputeCapabilities(value: unknown): ComputeCapabilitiesDto | null {
  if (!value || typeof value !== "object") return null;
  const record = Array.isArray(value) ? null : (value as Record<string, unknown>);
  const rawOptions = Array.isArray(value)
    ? value
    : Array.isArray(record?.options)
      ? record.options
      : Array.isArray(record?.devices)
        ? record.devices
        : [];
  const options = rawOptions
    .map((item): ComputeOptionDto | null => {
      if (!item || typeof item !== "object") return null;
      const option = item as Record<string, unknown>;
      const target = normalizeComputeTarget(option.target ?? option.device ?? option.kind ?? option.backend ?? option.id);
      const supported = Array.isArray(option.supportedComputeTypes)
        ? option.supportedComputeTypes.filter((type): type is string => typeof type === "string")
        : [];
      return {
        target,
        available: option.available === true,
        backend: typeof option.backend === "string" ? option.backend : null,
        reason:
          typeof option.reason === "string"
            ? option.reason
            : typeof option.publicFailureReason === "string"
              ? option.publicFailureReason
              : typeof option.failureReason === "string"
                ? option.failureReason
              : null,
        supportedComputeTypes: supported,
      };
    })
    .filter((item): item is ComputeOptionDto => item !== null);
  if (!options.length) return null;
  const target = normalizeComputeTarget(record?.active ?? record?.device);
  const automaticCandidate = options.find((option) => option.target === "nvidia" && option.available)
    ?? options.find((option) => option.target === "amd" && option.available)
    ?? options.find((option) => option.target === "cpu" && option.available);
  const automaticBackend = normalizeComputeTarget(record?.automaticBackend ?? record?.preferred ?? automaticCandidate?.target);
  return { active: target, automaticBackend, options };
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}
