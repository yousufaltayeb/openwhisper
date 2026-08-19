import type { ComputeCapabilitiesDto, ComputeOptionDto, ModelDto } from "./types";

/**
 * The allowlisted Faster Whisper names remain available while the engine is
 * loading its catalog. Engine records replace this list as soon as Settings
 * asks for `models.list`; this fallback also keeps browser fixtures useful.
 */
export const BUILT_IN_FASTER_WHISPER_MODELS: ModelDto[] = [
  model("tiny", "multilingual", "fastest", "light", 75_000_000),
  model("base", "multilingual", "very fast", "good", 145_000_000),
  model("small", "multilingual", "fast", "strong", 480_000_000),
  model("medium", "multilingual", "balanced", "very strong", 1_500_000_000),
  model("large-v1", "multilingual", "slow", "high", 3_000_000_000),
  model("large-v2", "multilingual", "slow", "high", 3_000_000_000),
  model("large-v3", "multilingual", "slow", "highest", 3_000_000_000, { aliases: ["large"] }),
  model("large-v3-turbo", "multilingual", "fast", "highest", 1_600_000_000, {
    installed: true,
    aliases: ["turbo"],
  }),
  model("tiny.en", "english", "fastest", "light", 75_000_000),
  model("base.en", "english", "very fast", "good", 145_000_000),
  model("small.en", "english", "fast", "strong", 480_000_000),
  model("medium.en", "english", "balanced", "very strong", 1_500_000_000),
  model("distil-small.en", "distilled", "fastest", "strong", 330_000_000),
  model("distil-medium.en", "distilled", "very fast", "very strong", 820_000_000),
  model("distil-large-v2", "distilled", "fast", "high", 1_500_000_000),
  model("distil-large-v3", "distilled", "fast", "high", 1_600_000_000),
  model("distil-large-v3.5", "distilled", "fast", "high", 1_600_000_000),
];

function model(
  id: string,
  group: ModelDto["group"],
  relativeSpeed: string,
  relativeQuality: string,
  sizeBytes: number,
  overrides: Partial<ModelDto> & { installed?: boolean } = {},
): ModelDto {
  return {
    id,
    displayName: id,
    group,
    languages: group === "english" ? ["English only"] : ["Arabic", "English", "100+ languages"],
    relativeSpeed,
    relativeQuality,
    sizeBytes,
    status: overrides.installed ? "installed" : "not-installed",
    progress: 0,
    error: null,
    supportsLiveTyping: true,
    aliases: [],
    ...overrides,
  };
}

export const DEFAULT_COMPUTE_CAPABILITIES: ComputeCapabilitiesDto = {
  active: "auto",
  automaticBackend: "cpu",
  options: [
    {
      target: "auto",
      available: true,
      backend: "cpu",
      reason: null,
      supportedComputeTypes: ["auto"],
    },
    {
      target: "cpu",
      available: true,
      backend: "cpu",
      reason: null,
      supportedComputeTypes: ["int8", "float32"],
    },
    unavailable("nvidia", "A validated NVIDIA runtime was not found."),
    unavailable("amd", "A validated ROCm runtime was not found."),
  ],
};

function unavailable(target: ComputeOptionDto["target"], reason: string): ComputeOptionDto {
  return {
    target,
    available: false,
    backend: null,
    reason,
    supportedComputeTypes: [],
  };
}

/** Keep aliases from creating a second selectable row for the same model. */
export function deduplicateModels(models: ModelDto[]): ModelDto[] {
  const aliases = new Set<string>();
  const canonicalAliasIds = new Set(["large", "turbo"]);
  return models.filter((item) => {
    if (canonicalAliasIds.has(item.id) && models.some((candidate) => candidate.id === "large-v3" || candidate.id === "large-v3-turbo")) {
      return false;
    }
    if (aliases.has(item.id)) return false;
    aliases.add(item.id);
    for (const alias of item.aliases) aliases.add(alias);
    return true;
  });
}

export function modelSizeLabel(sizeBytes: number | null): string {
  if (!sizeBytes || !Number.isFinite(sizeBytes)) return "Size unavailable";
  const gigabytes = sizeBytes / 1_000_000_000;
  if (gigabytes >= 1) return `${gigabytes.toFixed(gigabytes >= 10 ? 0 : 1)} GB`;
  return `${Math.round(sizeBytes / 1_000_000)} MB`;
}
