import {
  AlertCircle,
  Check,
  CircleStop,
  ClipboardCopy,
  Command,
  Info,
  Mic,
  Monitor,
  Moon,
  RotateCcw,
  Settings2,
  Sun,
  TriangleAlert,
  X,
} from "lucide-react";
import { Button } from "react-aria-components";

import type {
  DictationState,
  ProviderProgressStage,
  ThemePreference,
} from "../engine/types";
import type { OpenWhisperState } from "../state/openWhisperReducer";
import { AudioMeter } from "./AudioMeter";
import styles from "./CaptureSurface.module.css";

interface CaptureSurfaceProps {
  state: OpenWhisperState;
  onStart: () => Promise<unknown>;
  onStop: () => Promise<unknown>;
  onCancel: () => Promise<unknown>;
  onCopyTranscript: () => Promise<void>;
  onDismissNotice: () => void;
  onOpenCommands: () => void;
  onOpenSettings: () => void;
  onSetTheme: (theme: ThemePreference) => Promise<void>;
}

const activeStates: DictationState[] = ["recording", "processing", "cleaning", "inserting"];

export function CaptureSurface({
  state,
  onStart,
  onStop,
  onCancel,
  onCopyTranscript,
  onDismissNotice,
  onOpenCommands,
  onOpenSettings,
  onSetTheme,
}: CaptureSurfaceProps) {
  if (state.connection === "booting") {
    return <BootingSurface />;
  }
  if (state.connection === "fatal") {
    return <FatalSurface message={state.fatalMessage} />;
  }

  const isRecording = state.dictationState === "recording";
  const isProcessing = ["processing", "cleaning", "inserting"].includes(
    state.dictationState,
  );
  const provider = state.providers.find(
    (candidate) => candidate.id === state.settings?.transcriptionProvider,
  );
  const providerReady = provider?.available === true;
  const captureDisabled =
    isProcessing || state.pendingAction !== null || (!isRecording && !providerReady);
  const transcript = state.transcript || state.finalTranscript;
  const nextTheme = cycleTheme(state.theme);
  const ThemeIcon = themeIcon(state.theme);
  const operationLabel = stateLabel(
    state.dictationState,
    state.providerProgressStage,
    state.insertionResult,
  );

  return (
    <main className={styles.surface}>
      <header className={styles.topRail}>
        <div className={styles.identity}>
          <BrandSignal />
          <span className={styles.brandName}>OpenWhisper</span>
          <span className={styles.railSeam} aria-hidden="true" />
          <h1>Capture</h1>
        </div>
        <div className={styles.topActions}>
          <StateReadout
            state={state.dictationState}
            elapsed={state.recordingElapsed}
            providerStage={state.providerProgressStage}
            insertionResult={state.insertionResult}
          />
          <Button
            className={styles.commandButton}
            onPress={onOpenCommands}
            aria-label="Open command drawer"
          >
            <Command aria-hidden="true" size={17} />
            <span>Commands</span>
            <kbd>Ctrl K</kbd>
          </Button>
          <Button
            className={styles.commandButton}
            onPress={onOpenSettings}
            aria-label="Open settings"
          >
            <Settings2 aria-hidden="true" size={17} />
            <span>Settings</span>
          </Button>
          <Button
            className={styles.iconButton}
            aria-label={`Theme: ${state.theme}. Switch to ${nextTheme}.`}
            onPress={() => void onSetTheme(nextTheme)}
          >
            <ThemeIcon aria-hidden="true" size={18} />
          </Button>
        </div>
      </header>

      {state.notice ? (
        <div className={styles.notice} data-level={state.notice.level} role={state.notice.level === "error" ? "alert" : "status"}>
          <NoticeIcon level={state.notice.level} />
          <span>{state.notice.message}</span>
          <Button className={styles.noticeDismiss} onPress={onDismissNotice} aria-label="Dismiss notice">
            <X aria-hidden="true" size={16} />
          </Button>
        </div>
      ) : null}

      <section className={styles.bench} aria-label="Dictation capture instrument">
        <div className={styles.controlPlate}>
          <div className={styles.plateHeading}>
            <h2>{isRecording ? "Recording" : isProcessing ? operationLabel : "Record control"}</h2>
            <span className={styles.mono}>{state.settings?.activeModeId ?? "raw"}</span>
          </div>

          <div className={styles.primaryControlArea}>
            <Button
              className={`${styles.recordButton} ${isRecording ? styles.recording : ""}`}
              isDisabled={captureDisabled}
              onPress={() => void (isRecording ? onStop() : onStart())}
              aria-label={isRecording ? "Stop dictation" : "Start dictation"}
              aria-describedby={!providerReady ? "capture-readiness" : undefined}
            >
              {isRecording ? (
                <CircleStop aria-hidden="true" size={42} strokeWidth={1.55} />
              ) : (
                <Mic aria-hidden="true" size={42} strokeWidth={1.55} />
              )}
              <span>
                {isRecording
                  ? "Stop"
                  : isProcessing
                    ? state.providerProgressStage === "loading_model"
                      ? "Loading"
                      : "Working"
                    : state.pendingAction === "dictation.start"
                      ? "Starting"
                      : "Record"}
              </span>
            </Button>
            {activeStates.includes(state.dictationState) ? (
              <>
                <Button className={styles.cancelButton} onPress={() => void onCancel()}>
                  <X aria-hidden="true" size={16} />
                  Cancel
                </Button>
                {state.providerProgressStage === "loading_model" ? (
                  <p className={styles.controlHint} role="status">
                    Preparing the local model. The first run can take longer.
                  </p>
                ) : null}
              </>
            ) : (
              <p className={styles.controlHint} id="capture-readiness">
                {providerReady
                  ? "Speak in Arabic, English, or both."
                  : provider?.unavailableReason ?? "Choose an available transcription provider."}
              </p>
            )}
          </div>

          <AudioMeter
            level={state.audioLevel}
            peak={state.audioPeak}
            active={isRecording}
          />
        </div>

        <div className={styles.transcriptPlate}>
          <div className={styles.plateHeading}>
            <h2>Live transcript</h2>
            <span className={styles.mono}>{transcript ? "signal acquired" : "awaiting signal"}</span>
          </div>
          <div
            className={`${styles.transcript} ${transcript ? "" : styles.transcriptEmpty}`}
            aria-label="Live transcript"
            dir="auto"
          >
            {transcript || "Your words will appear here while OpenWhisper listens."}
          </div>
          <div className={styles.transcriptFooter}>
            <span>{transcript ? "Automatic direction · interface memory only" : "Transcript state stays in memory"}</span>
            {state.insertionResult === "failed" && state.finalTranscript ? (
              <Button className={styles.copyRecovery} onPress={() => void onCopyTranscript()}>
                <ClipboardCopy aria-hidden="true" size={15} />
                Copy preserved text
              </Button>
            ) : null}
            <span className={styles.mono} dir="ltr">
              {state.settings?.language === "auto"
                ? "language auto"
                : `language ${state.settings?.language}`}
            </span>
          </div>
        </div>
      </section>

      <dl className={styles.readingRail}>
        <Reading label="Shortcut" value={normalizeShortcut(state.settings?.shortcut)} mono />
        <Reading
          label="Provider / model"
          value={providerModelLabel(
            providerReady ? provider?.name : provider?.unavailableReason,
            providerReady ? state.settings?.transcriptionModel : null,
          )}
        />
        <Reading
          label="Microphone"
          value={microphoneLabel(state.settings?.audioDeviceId)}
          mono={Boolean(state.settings?.audioDeviceId)}
        />
        <Reading
          label="Privacy boundary"
          value={provider?.needsApiKey ? "Configured cloud provider" : "Local engine"}
        />
        <Reading
          label="Insertion result"
          value={insertionLabel(state.insertionResult)}
          status={state.insertionResult}
        />
      </dl>
    </main>
  );
}

function BrandSignal() {
  return (
    <span className={styles.brandSignal} aria-hidden="true">
      {[2, 5, 8, 5, 2].map((height, index) => (
        <i key={`${height}-${index}`} style={{ blockSize: `${height}px` }} />
      ))}
    </span>
  );
}

function StateReadout({
  state,
  elapsed,
  providerStage,
  insertionResult,
}: {
  state: DictationState;
  elapsed: number;
  providerStage: ProviderProgressStage | null;
  insertionResult: OpenWhisperState["insertionResult"];
}) {
  const displayState =
    state === "completed" && insertionResult === "failed" ? "failed" : state;
  const label = stateLabel(state, providerStage, insertionResult);
  const accessibleLabel = state === "recording" ? `${label}, ${formatElapsed(elapsed)}` : label;
  return (
    <div className={styles.stateReadout} role="status" aria-live="polite" aria-label={accessibleLabel}>
      <span className={styles.stateDot} data-state={displayState} aria-hidden="true" />
      <span>{label}</span>
      {state === "recording" ? <time className={styles.mono}>{formatElapsed(elapsed)}</time> : null}
    </div>
  );
}

function NoticeIcon({ level }: { level: "warning" | "error" | "info" }) {
  const Icon = level === "error" ? AlertCircle : level === "warning" ? TriangleAlert : Info;
  return <Icon aria-hidden="true" size={17} />;
}

function Reading({
  label,
  value,
  mono = false,
  status,
}: {
  label: string;
  value: string;
  mono?: boolean;
  status?: OpenWhisperState["insertionResult"];
}) {
  return (
    <div className={styles.reading}>
      <dt>{label}</dt>
      <dd className={mono ? styles.mono : undefined} dir="auto" title={value}>
        {status === "inserted" ? <Check aria-hidden="true" size={16} /> : null}
        {status === "copied" ? <ClipboardCopy aria-hidden="true" size={16} /> : null}
        {status === "both" ? (
          <>
            <Check aria-hidden="true" size={16} />
            <ClipboardCopy aria-hidden="true" size={16} />
          </>
        ) : null}
        {status === "failed" ? <AlertCircle aria-hidden="true" size={16} /> : null}
        {value}
      </dd>
    </div>
  );
}

function providerModelLabel(provider: string | null | undefined, model: string | null | undefined) {
  if (!provider) return "Unavailable";
  return model ? `${provider} · ${model}` : provider;
}

function microphoneLabel(deviceId: string | null | undefined) {
  return deviceId?.trim() || "System default";
}

function BootingSurface() {
  return (
    <main className={styles.booting} aria-busy="true">
      <BrandSignal />
      <h1>Starting the local engine</h1>
      <p>Connecting capture, shortcuts, and insertion.</p>
      <div className={styles.bootingRule} aria-hidden="true" />
    </main>
  );
}

function FatalSurface({ message }: { message: string | null }) {
  return (
    <main className={styles.fatal}>
      <AlertCircle aria-hidden="true" size={30} />
      <h1>Capture stopped safely</h1>
      <p>{message ?? "The local engine is unavailable."}</p>
      <p className={styles.fatalDetail}>
        OpenWhisper will not restart silently during a recording. Temporary audio recovery runs when
        the engine starts again.
      </p>
      <Button className={styles.recoveryButton} onPress={() => window.location.reload()}>
        <RotateCcw aria-hidden="true" size={17} />
        Restart OpenWhisper
      </Button>
    </main>
  );
}

function stateLabel(
  state: DictationState,
  providerStage: ProviderProgressStage | null,
  insertionResult: OpenWhisperState["insertionResult"],
) {
  if (state === "processing") {
    const progressLabels: Partial<Record<ProviderProgressStage, string>> = {
      queued: "Queued",
      loading_model: "Loading local model",
      requesting: "Contacting provider",
      transcribing: "Transcribing",
      cleaning: "Cleaning",
    };
    return progressLabels[providerStage ?? "transcribing"] ?? "Transcribing";
  }
  if (state === "completed") {
    if (insertionResult === "copied") return "Copied";
    if (insertionResult === "both") return "Inserted and copied";
    if (insertionResult === "failed") return "Needs attention";
  }
  const labels: Record<DictationState, string> = {
    idle: "Ready",
    recording: "Listening",
    processing: "Transcribing",
    cleaning: "Cleaning",
    inserting: "Inserting",
    completed: "Inserted",
    cancelled: "Cancelled",
    failed: "Needs attention",
  };
  return labels[state];
}

function insertionLabel(result: OpenWhisperState["insertionResult"]): string {
  return {
    waiting: "Waiting",
    inserted: "Inserted",
    copied: "Copied — paste in target",
    both: "Inserted + copied",
    cancelled: "Cancelled",
    failed: "Not inserted",
  }[result];
}

function normalizeShortcut(shortcut: string | undefined) {
  return (shortcut ?? "Alt + O")
    .split("<")
    .join("")
    .split(">")
    .join("")
    .split("+")
    .join(" + ")
    .split("  ")
    .join(" ");
}

const twoDigits = new Intl.NumberFormat("en", { minimumIntegerDigits: 2 });

function formatElapsed(elapsed: number) {
  const wholeSeconds = Math.max(0, Math.floor(elapsed));
  const minutes = Math.floor(wholeSeconds / 60);
  const seconds = wholeSeconds % 60;
  return `${twoDigits.format(minutes)}:${twoDigits.format(seconds)}`;
}

function cycleTheme(theme: ThemePreference): ThemePreference {
  return theme === "system" ? "light" : theme === "light" ? "dark" : "system";
}

function themeIcon(theme: ThemePreference) {
  return theme === "system" ? Monitor : theme === "light" ? Sun : Moon;
}
