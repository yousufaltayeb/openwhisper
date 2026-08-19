import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  CircleHelp,
  CloudDownload,
  Cpu,
  HardDrive,
  LockKeyhole,
  RefreshCw,
  Settings2,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  Button,
  Dialog,
  Heading,
  Modal,
  ModalOverlay,
} from "react-aria-components";

import {
  normalizeComputeCapabilities,
  normalizeComputeTarget,
  normalizeModels,
  normalizeOutputMode,
  type ComputeCapabilitiesDto,
  type ComputeOptionDto,
  type ComputeTarget,
  type ModelDto,
  type OutputMode,
  type SettingsDto,
} from "../engine/types";
import { BUILT_IN_FASTER_WHISPER_MODELS, DEFAULT_COMPUTE_CAPABILITIES, deduplicateModels, modelSizeLabel } from "../engine/modelCatalog";
import styles from "./Settings.module.css";

export interface SettingsDialogProps {
  isOpen: boolean;
  settings: SettingsDto;
  models: ModelDto[];
  compute: ComputeCapabilitiesDto | null;
  isReadOnly: boolean;
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>;
  onOpenChange: (isOpen: boolean) => void;
  onSaved: (settings: SettingsDto) => void;
  onModelsLoaded: (models: unknown) => void;
  onComputeLoaded: (compute: unknown) => void;
  onRestartEngine: () => Promise<void>;
}

interface SettingsDraft extends SettingsDto {
  outputMode: OutputMode;
}

type ModelAction = "download" | "cancel" | "remove";

const computeLabels: Record<ComputeTarget, string> = {
  auto: "Automatic",
  cpu: "CPU",
  nvidia: "NVIDIA GPU",
  amd: "AMD GPU",
};

const computeDescriptions: Record<ComputeTarget, string> = {
  auto: "Uses validated NVIDIA, then AMD, then CPU.",
  cpu: "Runs with the CPU-only CTranslate2 runtime.",
  nvidia: "Uses the pinned CUDA extension when validated.",
  amd: "Uses the ROCm extension when validated.",
};

const modelGroupLabels: Record<ModelDto["group"], string> = {
  multilingual: "Multilingual",
  english: "English only",
  distilled: "Distilled",
  legacy: "Legacy",
};

export function SettingsDialog({
  isOpen,
  settings,
  models,
  compute,
  isReadOnly,
  request,
  onOpenChange,
  onSaved,
  onModelsLoaded,
  onComputeLoaded,
  onRestartEngine,
}: SettingsDialogProps) {
  const [draft, setDraft] = useState<SettingsDraft>(() => toDraft(settings));
  const [discardOpen, setDiscardOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [restartRequired, setRestartRequired] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [modelAction, setModelAction] = useState<string | null>(null);
  const [computeLoading, setComputeLoading] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setDraft(toDraft(settings));
    setDiscardOpen(false);
    setSaveError("");
    setRestartRequired(false);
    setLoadError("");
    setModelAction(null);
    void loadModels();
    void loadCompute();
    // Settings is intentionally rehydrated only when it opens. While it is
    // open, the draft is the user's staged source of truth.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const catalog = useMemo(() => {
    const source = models.length ? models : BUILT_IN_FASTER_WHISPER_MODELS;
    return deduplicateModels(source);
  }, [models]);
  const selectedModel = findCatalogSelection(catalog, draft.transcriptionModel);
  const savedModel = findCatalogSelection(catalog, settings.transcriptionModel);
  const unknownLegacyModel = selectedModel
    ? null
    : draft.transcriptionModel;
  const activeCompute = compute ?? DEFAULT_COMPUTE_CAPABILITIES;
  const options = completeComputeOptions(activeCompute.options);
  const dirty = hasDraftChanges(settings, draft);
  const isFasterWhisper = draft.transcriptionProvider === "faster-whisper";
  const isRawMode = draft.activeModeId === "raw" && draft.cleanupMode === "raw";
  const liveTypingBlocked = draft.liveInsertion && (!isFasterWhisper || !isRawMode);
  const selectedEnglishOnly = selectedModel?.group === "english";
  const selectedLanguage = draft.language.toLowerCase();
  const languageWarning =
    selectedEnglishOnly && selectedLanguage !== "auto" && selectedLanguage !== "en" && selectedLanguage !== "english";
  const modelNotReady = Boolean(
    selectedModel &&
      selectedModel.id !== savedModel?.id &&
      selectedModel.status !== "installed",
  );

  async function loadModels() {
    try {
      const result = await request<unknown>("models.list");
      const normalized = normalizeModels(result);
      if (normalized.length) onModelsLoaded(normalized);
    } catch {
      setLoadError("The model catalog could not be refreshed. Installed models remain available.");
    }
  }

  async function loadCompute() {
    setComputeLoading(true);
    try {
      // The engine exposes this as `compute.capabilities`; keep a list-method
      // fallback for older host adapters without making that mismatch visible.
      let result: unknown;
      try {
        result = await request<unknown>("compute.capabilities");
      } catch {
        result = await request<unknown>("compute.list");
      }
      if (normalizeComputeCapabilities(result)) onComputeLoaded(result);
    } catch {
      setLoadError((current) => current || "Hardware capability details are unavailable; CPU remains available.");
    } finally {
      setComputeLoading(false);
    }
  }

  async function probeCompute() {
    if (isReadOnly || computeLoading) return;
    setComputeLoading(true);
    setLoadError("");
    try {
      const result = await request<unknown>("compute.probe");
      if (normalizeComputeCapabilities(result)) onComputeLoaded(result);
      else await loadCompute();
    } catch {
      setLoadError("The hardware probe could not complete. Automatic mode will continue using CPU safely.");
    } finally {
      setComputeLoading(false);
    }
  }

  async function runModelAction(model: ModelDto, action: ModelAction) {
    if (isReadOnly || modelAction) return;
    setModelAction(`${action}:${model.id}`);
    setLoadError("");
    try {
      await request(`models.${action}`, action === "cancel" ? { modelId: model.id } : { modelId: model.id });
      await loadModels();
    } catch {
      setLoadError(
        action === "remove"
          ? "The model could not be removed. The selected model remains protected."
          : "The model operation could not complete. You can retry without losing a partial cache.",
      );
    } finally {
      setModelAction(null);
    }
  }

  function requestClose() {
    if (saving) return;
    if (dirty) {
      setDiscardOpen(true);
      return;
    }
    onOpenChange(false);
  }

  function updateDraft(changes: Partial<SettingsDraft>) {
    if (isReadOnly) return;
    setSaveError("");
    setDraft((current) => ({ ...current, ...changes }));
  }

  function useFasterWhisperRaw() {
    updateDraft({
      transcriptionProvider: "faster-whisper",
      activeModeId: "raw",
      cleanupMode: "raw",
      cleanupProvider: "none",
      customCleanupPrompt: "",
    });
  }

  async function save() {
    if (!dirty || saving || isReadOnly || liveTypingBlocked) return;
    setSaving(true);
    setSaveError("");
    const normalizedDevice = normalizeComputeTarget(draft.device);
    const canonicalModel = selectedModel?.id ?? draft.transcriptionModel;
    const changes: Record<string, unknown> = {
      transcriptionProvider: draft.transcriptionProvider,
      transcriptionModel: canonicalModel,
      device: normalizedDevice,
      liveInsertion: draft.liveInsertion,
      outputMode: draft.outputMode,
      cleanupMode: draft.cleanupMode,
      cleanupProvider: draft.cleanupProvider,
      customCleanupPrompt: draft.customCleanupPrompt,
      language: draft.language,
      activeModeId: draft.activeModeId,
    };
    const previousDevice = normalizeComputeTarget(settings.device);
    const nextSettings = { ...settings, ...changes, device: normalizedDevice, outputMode: draft.outputMode } as SettingsDto;
    try {
      await request("settings.update", { changes });
    } catch {
      setSaveError("OpenWhisper could not save these settings. Nothing in this dialog was applied partially.");
      setSaving(false);
      return;
    }

    onSaved(nextSettings);
    setDraft(toDraft(nextSettings));
    const acceleratorChanged =
      previousDevice !== normalizedDevice &&
      (previousDevice === "nvidia" ||
        previousDevice === "amd" ||
        normalizedDevice === "nvidia" ||
        normalizedDevice === "amd");
    if (acceleratorChanged || restartRequired) {
      try {
        await onRestartEngine();
        setRestartRequired(false);
      } catch {
        setRestartRequired(true);
        setSaveError("Settings were saved, but the engine could not restart. Retry the restart before dictating.");
        setSaving(false);
        return;
      }
    }
    setSaving(false);
    onOpenChange(false);
  }

  async function retryRestart() {
    if (saving || isReadOnly) return;
    setSaving(true);
    setSaveError("");
    try {
      await onRestartEngine();
      setRestartRequired(false);
      onOpenChange(false);
    } catch {
      setSaveError("Settings remain saved, but the engine restart failed again. You can retry or restart OpenWhisper.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <ModalOverlay
        className={styles.overlay}
        isOpen={isOpen}
        isDismissable={!saving}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) requestClose();
        }}
      >
        <Modal className={styles.modal} isDismissable={!saving}>
          <Dialog className={styles.dialog} aria-label="OpenWhisper settings">
            <div className={styles.header}>
              <div className={styles.headingContent}>
                <span className={styles.headerIcon} aria-hidden="true"><Settings2 size={21} /></span>
                <div>
                  <Heading slot="title">Settings</Heading>
                  <p>Speech processing and final delivery</p>
                </div>
              </div>
              <Button className={styles.closeButton} onPress={requestClose} aria-label="Close settings">
                <X aria-hidden="true" size={18} />
              </Button>
            </div>

            {isReadOnly ? (
              <div className={styles.readOnlyNotice} role="status">
                <LockKeyhole aria-hidden="true" size={17} />
                <span>Settings are read-only while dictation is active. Save becomes available when capture finishes.</span>
              </div>
            ) : null}
            {loadError ? <div className={styles.inlineError} role="alert"><AlertTriangle aria-hidden="true" size={17} />{loadError}</div> : null}

            <div className={styles.content}>
              <section className={styles.section} aria-labelledby="settings-speech-heading">
                <div className={styles.sectionHeading}>
                  <div>
                    <Heading id="settings-speech-heading" level={2}>Speech</Heading>
                    <p>Choose the local Faster Whisper model and validated compute path.</p>
                  </div>
                </div>

                <div className={styles.fieldGroup}>
                  <div className={styles.fieldLabel}>Transcription engine</div>
                  <div className={styles.engineRow}>
                    <div className={styles.engineMark} aria-hidden="true"><Zap size={17} /></div>
                    <div>
                      <strong>Faster Whisper</strong>
                      <span>Local speech-to-text with no raw audio in the interface.</span>
                    </div>
                    <span className={styles.badge}>LOCAL</span>
                  </div>
                </div>

                <div className={styles.fieldGroup}>
                  <div className={styles.fieldHeadingRow}>
                    <div>
                      <div className={styles.fieldLabel}>Model</div>
                      <p className={styles.fieldHelp}>Arabic and mixed-direction speech works with multilingual models. English-only models are marked clearly.</p>
                      <a className={styles.sectionAnchor} href="#settings-compute">Jump past the model catalog to Compute</a>
                    </div>
                    <span className={styles.modelCount}>{catalog.length} models</span>
                  </div>
                  <div
                    className={styles.modelGroups}
                    role="radiogroup"
                    aria-label="Faster Whisper speech model"
                  >
                    {(["multilingual", "english", "distilled"] as const).map((group) => {
                      const groupModels = catalog.filter((model) => model.group === group);
                      if (!groupModels.length) return null;
                      return (
                        <div className={styles.modelGroup} key={group}>
                          <h3>{modelGroupLabels[group]}</h3>
                          <div className={styles.modelList}>
                            {groupModels.map((model) => (
                              <ModelRow
                                key={model.id}
                                model={model}
                                selected={selectedModel?.id === model.id}
                                disabled={isReadOnly}
                                busy={modelAction !== null}
                                actionKey={modelAction}
                                onSelect={() => updateDraft({ transcriptionModel: model.id })}
                                onAction={(action) => void runModelAction(model, action)}
                              />
                            ))}
                          </div>
                        </div>
                      );
                    })}
                    {unknownLegacyModel ? (
                      <div className={styles.legacyModel} role="status">
                        <AlertTriangle aria-hidden="true" size={17} />
                        <div>
                          <strong>Legacy model retained</strong>
                          <span dir="auto">{unknownLegacyModel}</span>
                          <small>This model is not in the current catalog. It stays selected until you choose another model.</small>
                        </div>
                      </div>
                    ) : null}
                  </div>
                  {languageWarning ? (
                    <p className={styles.warning} role="alert"><AlertTriangle aria-hidden="true" size={16} />English-only models cannot transcribe the selected <bdi dir="auto">{draft.language}</bdi> language.</p>
                  ) : null}
                  {modelNotReady ? (
                    <p className={styles.warning} role="alert"><CloudDownload aria-hidden="true" size={16} />Download this model completely before saving it as the active model.</p>
                  ) : null}
                  {selectedModel?.status === "installed" ? <p className={styles.installedNote}><Check aria-hidden="true" size={16} />Installed size {modelSizeLabel(selectedModel.sizeBytes)}</p> : null}
                </div>

                <div className={styles.fieldGroup} id="settings-compute">
                  <div className={styles.fieldHeadingRow}>
                    <div>
                      <div className={styles.fieldLabel}>Compute</div>
                      <p className={styles.fieldHelp}>Automatic probes the complete local Whisper path and prefers a validated NVIDIA GPU, then AMD, then CPU.</p>
                    </div>
                    <Button className={styles.quietButton} onPress={() => void probeCompute()} isDisabled={isReadOnly || computeLoading}>
                      <RefreshCw aria-hidden="true" size={15} />{computeLoading ? "Probing" : "Probe again"}
                    </Button>
                  </div>
                  <div className={styles.computeList} role="radiogroup" aria-label="Compute device">
                    {options.map((option) => (
                      <ComputeRow key={option.target} option={option} selected={normalizeComputeTarget(draft.device) === option.target} disabled={isReadOnly || !option.available} onSelect={() => updateDraft({ device: option.target })} />
                    ))}
                  </div>
                </div>
              </section>

              <section className={styles.section} aria-labelledby="settings-output-heading">
                <div className={styles.sectionHeading}>
                  <div>
                    <Heading id="settings-output-heading" level={2}>Output</Heading>
                    <p>Control live typing separately from what happens when speech is finalized.</p>
                  </div>
                </div>

                <div className={styles.fieldGroup}>
                  <label className={styles.toggleRow} data-disabled={isReadOnly}>
                    <input type="checkbox" checked={draft.liveInsertion} disabled={isReadOnly} onChange={(event) => updateDraft({ liveInsertion: event.target.checked })} />
                    <span className={styles.toggleTrack} aria-hidden="true"><span /></span>
                    <span className={styles.toggleCopy}><strong>Type while speaking</strong><small>Insert only the reconciled suffix as you speak. The complete final transcript is preserved for clipboard delivery.</small></span>
                  </label>
                  {liveTypingBlocked ? (
                    <div className={styles.remedy} role="alert">
                      <AlertTriangle aria-hidden="true" size={17} />
                      <div><strong>Live typing needs Faster Whisper / Raw mode.</strong><span>Choose the explicit remedy to switch the engine and cleanup mode, or turn this control off.</span></div>
                      <Button className={styles.remedyButton} onPress={useFasterWhisperRaw} isDisabled={isReadOnly}>Use Faster Whisper / Raw mode</Button>
                    </div>
                  ) : null}
                </div>

                <div className={styles.fieldGroup}>
                  <div className={styles.fieldLabel}>Final delivery</div>
                  <p className={styles.fieldHelp}>Insert uses the existing clipboard fallback. Copy never attempts insertion. Insert + copy performs both.</p>
                  <div className={styles.outputList} role="radiogroup" aria-label="Final transcript delivery">
                    <OutputChoice mode="insert" selected={draft.outputMode === "insert"} disabled={isReadOnly} onSelect={() => updateDraft({ outputMode: "insert" })} />
                    <OutputChoice mode="clipboard" selected={draft.outputMode === "clipboard"} disabled={isReadOnly} onSelect={() => updateDraft({ outputMode: "clipboard" })} />
                    <OutputChoice mode="both" selected={draft.outputMode === "both"} disabled={isReadOnly} onSelect={() => updateDraft({ outputMode: "both" })} />
                  </div>
                </div>

                <div className={styles.deliveryNote}>
                  <HardDrive aria-hidden="true" size={17} />
                  <span>Raw audio remains in the Python engine. Clipboard delivery receives the complete finalized transcript.</span>
                </div>
              </section>
            </div>

            <footer className={styles.footer}>
              <div className={styles.footerStatus} role="status" aria-live="polite">
                {saveError ? <span className={styles.saveError}>{saveError}</span> : dirty ? <span>Unsaved changes</span> : <span>All changes saved</span>}
              </div>
              <div className={styles.footerActions}>
                <Button className={styles.cancelFooterButton} onPress={requestClose} isDisabled={saving}>Cancel</Button>
                {restartRequired ? (
                  <Button className={styles.saveButton} onPress={() => void retryRestart()} isDisabled={isReadOnly || saving}>
                    <RefreshCw aria-hidden="true" size={15} />{saving ? "Restarting" : "Retry restart"}
                  </Button>
                ) : (
                  <Button className={styles.saveButton} onPress={() => void save()} isDisabled={isReadOnly || saving || !dirty || liveTypingBlocked || modelNotReady}>
                    {saving ? "Saving" : "Save settings"}
                  </Button>
                )}
              </div>
            </footer>
          </Dialog>
        </Modal>
      </ModalOverlay>

      <DiscardDialog
        isOpen={discardOpen}
        onCancel={() => setDiscardOpen(false)}
        onDiscard={() => {
          setDiscardOpen(false);
          onOpenChange(false);
        }}
      />
    </>
  );
}

function ModelRow({
  model,
  selected,
  disabled,
  busy,
  actionKey,
  onSelect,
  onAction,
}: {
  model: ModelDto;
  selected: boolean;
  disabled: boolean;
  busy: boolean;
  actionKey: string | null;
  onSelect: () => void;
  onAction: (action: ModelAction) => void;
}) {
  const modelBusy = actionKey?.endsWith(`:${model.id}`) === true;
  const status = model.status;
  const statusLabel =
    status === "installed"
      ? `Installed · ${modelSizeLabel(model.sizeBytes)}`
      : status === "downloading"
        ? `Downloading · ${Math.round(model.progress)}%`
        : status === "error"
          ? "Download failed"
          : status === "cancelled"
            ? "Download paused"
            : `Download · ${modelSizeLabel(model.sizeBytes)}`;
  return (
    <div className={styles.modelRow} data-selected={selected} data-status={status} data-disabled={disabled}>
      <label className={styles.modelChoice} data-disabled={disabled}>
        <input type="radio" name="faster-whisper-model" checked={selected} disabled={disabled} onChange={onSelect} />
        <span className={styles.radioMark} aria-hidden="true" />
        <span className={styles.modelCopy}>
          <strong dir="auto">{model.displayName}</strong>
          <span dir="auto">{model.languages.join(" · ")}</span>
          <small dir="auto">{model.relativeSpeed} speed · {model.relativeQuality} quality</small>
        </span>
      </label>
      <div className={styles.modelActions}>
        {status === "downloading" ? <progress value={model.progress} max={100} aria-label={`Downloading ${model.displayName}`} /> : null}
        <span className={styles.modelStatus} dir="auto">{statusLabel}</span>
        {status === "installed" ? (
          <Button className={styles.modelActionButton} onPress={() => onAction("remove")} isDisabled={disabled || busy || selected} aria-label={selected ? `${model.displayName} is active and cannot be removed` : `Remove ${model.displayName}`}>
            <Trash2 aria-hidden="true" size={15} />Remove
          </Button>
        ) : status === "downloading" ? (
          <Button className={styles.modelActionButton} onPress={() => onAction("cancel")} isDisabled={disabled || busy} aria-label={`${modelBusy ? "Cancelling download of" : "Cancel download of"} ${model.displayName}`}>
            {modelBusy ? "Cancelling" : "Cancel"}
          </Button>
        ) : (
          <Button className={styles.modelActionButton} onPress={() => onAction("download")} isDisabled={disabled || busy} aria-label={`${modelBusy ? "Starting download of" : status === "error" || status === "cancelled" ? "Retry download of" : "Download"} ${model.displayName}`}>
            <CloudDownload aria-hidden="true" size={15} />{modelBusy ? "Starting" : status === "error" || status === "cancelled" ? "Retry" : "Download"}
          </Button>
        )}
      </div>
    </div>
  );
}

function ComputeRow({
  option,
  selected,
  disabled,
  onSelect,
}: {
  option: ComputeOptionDto;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <label className={styles.computeRow} data-selected={selected} data-disabled={disabled}>
      <input type="radio" name="compute-target" checked={selected} disabled={disabled} onChange={onSelect} />
      <span className={styles.radioMark} aria-hidden="true" />
      <span className={styles.computeIcon} aria-hidden="true">{option.target === "cpu" ? <Cpu size={17} /> : <Zap size={17} />}</span>
      <span className={styles.computeCopy}><strong>{computeLabels[option.target]}</strong><small dir="auto">{option.available ? option.backend ? `${option.backend} · ${computeDescriptions[option.target]}` : computeDescriptions[option.target] : option.reason ?? "Unavailable after validation."}</small></span>
      {selected && option.available ? <Check className={styles.selectedIcon} aria-hidden="true" size={17} /> : null}
      {!option.available ? <CircleHelp className={styles.unavailableIcon} aria-hidden="true" size={16} /> : null}
    </label>
  );
}

function OutputChoice({
  mode,
  selected,
  disabled,
  onSelect,
}: {
  mode: OutputMode;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const labels: Record<OutputMode, { title: string; description: string }> = {
    insert: { title: "Insert", description: "Type into the focused app; use clipboard fallback if needed." },
    clipboard: { title: "Copy", description: "Copy the complete final transcript without inserting." },
    both: { title: "Insert + copy", description: "Insert, then leave the complete final transcript on the clipboard." },
  };
  return (
    <label className={styles.outputChoice} data-selected={selected} data-disabled={disabled}>
      <input type="radio" name="output-mode" checked={selected} disabled={disabled} onChange={onSelect} />
      <span className={styles.radioMark} aria-hidden="true" />
      <span><strong>{labels[mode].title}</strong><small>{labels[mode].description}</small></span>
    </label>
  );
}

function DiscardDialog({
  isOpen,
  onCancel,
  onDiscard,
}: {
  isOpen: boolean;
  onCancel: () => void;
  onDiscard: () => void;
}) {
  return (
    <ModalOverlay className={styles.confirmOverlay} isOpen={isOpen} isDismissable onOpenChange={(open) => !open && onCancel()}>
      <Modal className={styles.confirmModal}>
        <Dialog className={styles.confirmDialog} aria-label="Discard settings changes">
          <AlertTriangle aria-hidden="true" size={23} />
          <Heading slot="title">Discard unsaved changes?</Heading>
          <p>Your staged speech and output choices will be lost.</p>
          <div className={styles.confirmActions}>
            <Button className={styles.cancelFooterButton} onPress={onCancel}>Keep editing</Button>
            <Button className={styles.discardButton} onPress={onDiscard}>Discard changes</Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function toDraft(settings: SettingsDto): SettingsDraft {
  return {
    ...settings,
    device: normalizeComputeTarget(settings.device),
    outputMode: normalizeOutputMode(settings.outputMode),
  };
}

function hasDraftChanges(settings: SettingsDto, draft: SettingsDraft) {
  const original = toDraft(settings);
  return JSON.stringify(original) !== JSON.stringify(draft);
}

function completeComputeOptions(options: ComputeOptionDto[]): ComputeOptionDto[] {
  const fallback = DEFAULT_COMPUTE_CAPABILITIES.options;
  const byTarget = new Map(options.map((option) => [option.target, option]));
  return fallback.map((option) => byTarget.get(option.target) ?? option);
}

function findCatalogSelection(catalog: ModelDto[], selection: string): ModelDto | undefined {
  return catalog.find(
    (model) =>
      !model.unknownLegacy &&
      (model.id === selection || model.aliases.includes(selection)),
  ) ?? catalog.find((model) => !model.unknownLegacy && model.selected);
}
