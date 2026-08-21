/*
THESIS: A live operations board keeps capture truth visible everywhere and refuses the generic sidebar dashboard.
OWN-WORLD: Matte graphite rails, chalk work surfaces, one recording-red signal, ruled rows, and measured labels.
STORY: See daemon safety, switch one task view, act, and retain capture context without leaving the board.
FIRST VIEWPORT: Status rail first, numbered view strip second, one full-width work stage, terse key dock at bottom.
FORM: Seventh grounded Operate structure; seed 92e40ae4.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
*/
import type { IpcClient } from "./ipc";
import type { JsonValue } from "./protocol.generated";
import {
  SETTINGS,
  cycleSetting,
  displaySettingValue,
  initialEditorValue,
  parseEditorValue,
  renderSettings,
  settingValue,
  type SettingsEditor,
} from "./settings";
import { BoxRenderable, TextRenderable, createCliRenderer, CliRenderEvents, type CliRenderer } from "@opentui/core";

export const VIEW_NAMES = [
  "Capture",
  "History",
  "Modes",
  "Words",
  "Models",
  "Settings",
  "Doctor",
  "Logs",
] as const;

export type ViewName = (typeof VIEW_NAMES)[number];

interface SceneState {
  view: ViewName;
  status: Record<string, unknown>;
  content: string;
  notice: string;
  loadGeneration: number;
  busy: boolean;
  modelActionArmed?: { action: "install" | "remove"; name: string };
  modelSelection: number;
  models?: Array<Record<string, unknown>>;
  providers?: JsonValue;
  settingsConfig?: Record<string, unknown>;
  settingsEditor?: SettingsEditor;
  settingsSelection: number;
  lastResult?: Record<string, unknown>;
  audioLevel?: Record<string, unknown>;
  scrollOffset: number;
}

type Request = (method: string, params?: JsonValue) => Promise<JsonValue>;

const COLOR = {
  canvas: "#151716",
  rail: "#111312",
  face: "#1c1e1d",
  faceSecondary: "#222423",
  ink: "#f0eee7",
  muted: "#aaa9a3",
  seam: "#464845",
  seamStrong: "#696b68",
  record: "#ef6a5c",
  recordInk: "#171817",
  danger: "#f08b7d",
};

export async function runTui(client: IpcClient): Promise<void> {
  const renderer = await createCliRenderer({
    screenMode: "alternate-screen",
    clearOnShutdown: true,
    exitOnCtrlC: false,
    exitSignals: ["SIGINT", "SIGTERM", "SIGHUP"],
    useMouse: true,
    backgroundColor: COLOR.canvas,
    targetFps: 30,
  });
  renderer.setTerminalTitle("OpenWhisper");
  let elapsedTimer: ReturnType<typeof setInterval> | undefined;
  try {
    const status = asRecord(await client.request("system.status"));
    const scene = buildTuiScene(renderer, status, (method, params) => client.request(method, params));
    elapsedTimer = setInterval(() => scene.refreshLayout(), 1_000);
    renderer.root.add(scene.root);
    let lastSequence = 0;
    let unsubscribe = client.subscribe(0, (event) => {
      lastSequence = event.sequence;
      if (event.type === "snapshot") scene.updateStatus(asRecord(event.state));
      if (event.type === "event") {
        if (event.event === "model.download.progress") scene.updateModelProgress(asRecord(event.data));
        else if (event.event === "recording.level") scene.updateAudioLevel(asRecord(event.data));
        else if (event.event === "transcription.preview") scene.updateTranscriptionPreview(asRecord(event.data));
        else if (event.event === "transcription.commit") scene.updateTranscriptionCommit(asRecord(event.data));
        else if (event.event === "insertion.state") scene.updateInsertionState(asRecord(event.data));
        else scene.setNotice(event.event.replaceAll(".", " "));
        if (event.event === "result.available") {
          const data = asRecord(event.data);
          if (typeof data.session_id === "string") void client.request("record.result", { session_id: data.session_id }).then((result) => scene.showResult(asRecord(result))).catch((error) => scene.setNotice(error instanceof Error ? error.message : "Final transcript could not be recovered."));
        }
        if (event.event !== "recording.level") void client.request("system.status").then((value) => scene.updateStatus(asRecord(value)));
      }
    });
    renderer.on(CliRenderEvents.RESIZE, () => scene.refreshLayout());
    await new Promise<void>((resolve) => {
      scene.onQuit(resolve);
      const removeDisconnect = client.onDisconnect(async () => {
        scene.setNotice("Daemon disconnected. Reconnecting (1/3)…");
        for (let attempt = 1; attempt <= 3; attempt += 1) {
          try {
            await Bun.sleep(attempt * 150);
            await client.connect();
            unsubscribe();
            unsubscribe = client.subscribe(lastSequence, (event) => {
              if (event.type === "snapshot") scene.updateStatus(asRecord(event.state));
              if (event.type === "event") {
                if (event.event === "recording.level") scene.updateAudioLevel(asRecord(event.data));
                else scene.setNotice(event.event.replaceAll(".", " "));
              }
            });
            scene.setNotice("Daemon reconnected. Current state restored.");
            return;
          } catch {
            if (attempt < 3) scene.setNotice(`Daemon disconnected. Reconnecting (${attempt + 1}/3)…`);
          }
        }
        scene.setNotice("Daemon remained unavailable after 3 attempts. Restoring the terminal.");
        removeDisconnect(); resolve();
      });
    });
    unsubscribe();
  } finally {
    if (elapsedTimer) clearInterval(elapsedTimer);
    renderer.destroy();
  }
}

export function buildTuiScene(
  renderer: CliRenderer,
  initialStatus: Record<string, unknown>,
  onRequest: Request,
) {
  const state: SceneState = {
    view: "Capture",
    status: initialStatus,
    content: viewContent("Capture", initialStatus, undefined, undefined, renderer.height <= 16),
    notice: captureFailure(initialStatus)
      ? `Capture failed · ${captureFailure(initialStatus)}`
      : "Connected. Audio never crosses the public IPC boundary.",
    loadGeneration: 0,
    busy: false,
    modelSelection: 0,
    settingsSelection: 0,
    scrollOffset: 0,
  };

  const root = new BoxRenderable(renderer, {
    id: "app",
    width: "100%",
    height: "100%",
    flexDirection: "column",
    backgroundColor: COLOR.canvas,
  });
  const statusText = new TextRenderable(renderer, {
    id: "status",
    height: 3,
    paddingX: 2,
    paddingY: 1,
    bg: COLOR.rail,
    fg: COLOR.ink,
    content: statusLine(state.status),
    truncate: true,
  });
  const navText = new TextRenderable(renderer, {
    id: "views",
    height: 3,
    paddingX: 2,
    paddingY: 1,
    bg: COLOR.faceSecondary,
    fg: COLOR.muted,
    content: navLine(state.view, renderer.width),
    truncate: true,
  });
  const stage = new BoxRenderable(renderer, {
    id: "stage",
    flexGrow: 1,
    marginX: 1,
    marginY: 1,
    padding: 2,
    border: true,
    borderStyle: "single",
    borderColor: COLOR.seamStrong,
    backgroundColor: COLOR.face,
    title: " CAPTURE ",
    titleColor: COLOR.ink,
  });
  const contentText = new TextRenderable(renderer, {
    id: "content",
    width: "100%",
    height: "100%",
    content: state.content,
    fg: COLOR.ink,
    bg: COLOR.face,
    wrapMode: "word",
    selectable: true,
  });
  const noticeText = new TextRenderable(renderer, {
    id: "notice",
    height: 2,
    paddingX: 2,
    bg: COLOR.faceSecondary,
    fg: COLOR.muted,
    content: state.notice,
    truncate: true,
  });
  const actionsText = new TextRenderable(renderer, {
    id: "actions",
    height: 2,
    paddingX: 2,
    bg: COLOR.rail,
    fg: COLOR.ink,
    content: actionLine(state.view, state.status, renderer.width, state.settingsEditor !== undefined),
    truncate: true,
  });
  stage.add(contentText);
  root.add(statusText);
  root.add(navText);
  root.add(stage);
  root.add(actionsText);
  root.add(noticeText);

  const quitListeners = new Set<() => void>();
  const refreshSettings = () => {
    if (state.view !== "Settings" || !state.settingsConfig) return;
    state.content = renderSettings(
      state.settingsConfig,
      state.settingsSelection,
      state.settingsEditor,
      renderer.width,
      renderer.width < 70 || renderer.height <= 18,
    );
    if (renderer.width < 70 || renderer.height <= 18) {
      state.scrollOffset = 0;
      return;
    }
    const selectedLine = 4 + state.settingsSelection;
    const visibleLines = Math.max(4, renderer.height - 12);
    if (selectedLine < state.scrollOffset) state.scrollOffset = selectedLine;
    if (selectedLine >= state.scrollOffset + visibleLines) {
      state.scrollOffset = selectedLine - visibleLines + 1;
    }
  };
  const refreshModels = () => {
    if (state.view !== "Models" || !state.models) return;
    state.content = `${formatModels(state.models as JsonValue, state.modelSelection)}\n\n${formatRows("Providers", state.providers ?? [], "No providers configured.")}`;
  };
  const refresh = () => {
    if (state.view === "Capture" && capturePhase(state.status) === "capturing") {
      state.content = viewContent("Capture", state.status, undefined, state.audioLevel, renderer.height <= 16);
    }
    refreshSettings();
    refreshModels();
    statusText.content = statusLine(state.status);
    navText.content = navLine(state.view, renderer.width);
    stage.title = ` ${state.view.toUpperCase()} `;
    const phase = capturePhase(state.status);
    stage.titleColor = phase === "capturing" ? COLOR.record : phase === "failed" ? COLOR.danger : COLOR.ink;
    contentText.content = visibleContent(state.content, state.scrollOffset, renderer.height);
    noticeText.content = state.notice;
    actionsText.content = actionLine(state.view, state.status, renderer.width, state.settingsEditor !== undefined);
  };

  const setFailure = (error: unknown) => {
    const message = error instanceof Error ? error.message : "The action failed.";
    const action = error && typeof error === "object" && "action" in error && typeof error.action === "string" ? error.action : undefined;
    state.notice = action ? `${message} · ${action}` : message;
    refresh();
  };

  const hydrateView = async (view: ViewName) => {
    const generation = ++state.loadGeneration;
    if (view !== "Capture") {
      state.content = `Loading ${view.toLowerCase()}…`;
      state.scrollOffset = 0;
      refresh();
    }
    try {
      if (view === "Settings") {
        const config = asRecord(await onRequest("config.list"));
        if (generation !== state.loadGeneration || view !== state.view) return;
        state.settingsConfig = config;
        state.scrollOffset = 0;
        refresh();
        return;
      }
      if (view === "Models") {
        const [models, providers] = await Promise.all([
          onRequest("models.list"),
          onRequest("providers.list"),
        ]);
        if (generation !== state.loadGeneration || view !== state.view) return;
        state.models = Array.isArray(models) ? models.map(asRecord) : [];
        state.providers = providers;
        state.modelSelection = Math.min(state.modelSelection, Math.max(0, state.models.length - 1));
        state.scrollOffset = 0;
        refresh();
        return;
      }
      const content = await loadView(view, onRequest, state.status);
      if (generation !== state.loadGeneration || view !== state.view) return;
      state.content = content;
      state.scrollOffset = 0;
      refresh();
    } catch (error) {
      if (generation !== state.loadGeneration || view !== state.view) return;
      setFailure(error);
      state.content = viewContent(view, state.status);
      state.scrollOffset = 0;
      refresh();
    }
  };

  const perform = async (method: string, params: JsonValue = null) => {
    if (state.busy) { state.notice = "The current action is still working."; refresh(); return; }
    state.busy = true;
    refresh();
    try {
      const result = await onRequest(method, params);
      state.notice = shortResult(result);
      if (method === "record.stop" && result && typeof result === "object" && !Array.isArray(result) && "final_text" in result) state.lastResult = asRecord(result);
      const status = await onRequest("system.status");
      state.status = asRecord(status);
      state.content = viewContent(state.view, state.status, state.view === "Capture" ? state.lastResult : undefined);
      refresh();
      if (state.view !== "Capture") await hydrateView(state.view);
    } catch (error) {
      setFailure(error);
    } finally {
      state.busy = false;
      refresh();
    }
  };

  const saveSetting = async (index: number, value: JsonValue) => {
    const spec = SETTINGS[index];
    if (!spec) return;
    if (spec.key.startsWith("model.") && (state.status.model_installing === true || !["idle", "failed"].includes(capturePhase(state.status)))) {
      state.notice = "Inference settings are disabled until recording, transcription, and model installation are idle.";
      refresh();
      return;
    }
    if (state.busy) {
      state.notice = "The current setting is still saving.";
      refresh();
      return;
    }
    state.busy = true;
    state.notice = `Saving ${spec.label}…`;
    refresh();
    try {
      await onRequest("config.set", { key: spec.key, value });
      const [config, status] = await Promise.all([
        onRequest("config.list"),
        onRequest("system.status"),
      ]);
      state.settingsConfig = asRecord(config);
      state.status = asRecord(status);
      state.notice = `${spec.label}: ${displaySettingValue(spec, value)} · saved`;
    } catch (error) {
      setFailure(error);
    } finally {
      state.busy = false;
      refresh();
    }
  };

  const cancelSettingsEditor = () => {
    const spec = SETTINGS[state.settingsEditor?.index ?? state.settingsSelection];
    delete state.settingsEditor;
    state.notice = `${spec?.label ?? "Setting"} edit cancelled.`;
    refresh();
  };

  const appendEditorText = (text: string) => {
    if (!state.settingsEditor) return;
    const clean = text.replace(/\p{Cc}/gu, "");
    if (clean.length === 0) return;
    state.settingsEditor.buffer = `${state.settingsEditor.buffer}${clean}`.slice(0, 160);
    refresh();
  };

  renderer.keyInput.on("keypress", (key) => {
    if (state.view === "Settings" && state.settingsEditor) {
      if ((key.ctrl && key.name === "c") || key.name === "escape") {
        cancelSettingsEditor();
        return;
      }
      if (isEnterKey(key.name)) {
        const editor = state.settingsEditor;
        const spec = SETTINGS[editor.index];
        if (!spec) return;
        try {
          const value = parseEditorValue(spec, editor.buffer);
          delete state.settingsEditor;
          void saveSetting(editor.index, value);
        } catch (error) {
          state.notice = error instanceof Error ? error.message : "That value is invalid.";
          refresh();
        }
        return;
      }
      if (key.name === "backspace" || key.name === "delete") {
        state.settingsEditor.buffer = Array.from(state.settingsEditor.buffer).slice(0, -1).join("");
        refresh();
        return;
      }
      if (!key.ctrl && !key.meta && !key.option && key.sequence.length > 0) appendEditorText(key.sequence);
      return;
    }
    if (key.ctrl && key.name === "c") {
      for (const listener of quitListeners) listener();
      return;
    }
    if (key.name === "q") {
      for (const listener of quitListeners) listener();
      return;
    }
    const index = Number(key.name) - 1;
    const selected = VIEW_NAMES[index];
    if (selected) {
      delete state.settingsEditor;
      state.view = selected;
      state.content = viewContent(selected, state.status);
      refresh();
      void hydrateView(selected);
      return;
    }
    if (state.view === "Settings") {
      if (key.name === "up" || key.name === "k") {
        state.settingsSelection = Math.max(0, state.settingsSelection - 1);
        refresh();
        return;
      }
      if (key.name === "down" || key.name === "j") {
        state.settingsSelection = Math.min(SETTINGS.length - 1, state.settingsSelection + 1);
        refresh();
        return;
      }
      if (key.name === "home") {
        state.settingsSelection = 0;
        refresh();
        return;
      }
      if (key.name === "end") {
        state.settingsSelection = SETTINGS.length - 1;
        refresh();
        return;
      }
      if (["pageup", "pageUp", "page_up"].includes(key.name)) {
        state.settingsSelection = Math.max(0, state.settingsSelection - 5);
        refresh();
        return;
      }
      if (["pagedown", "pageDown", "page_down"].includes(key.name)) {
        state.settingsSelection = Math.min(SETTINGS.length - 1, state.settingsSelection + 5);
        refresh();
        return;
      }
      const spec = SETTINGS[state.settingsSelection];
      if (!spec) return;
      if (key.name === "left" || key.name === "right") {
        const value = cycleSetting(
          spec,
          state.settingsConfig ? settingValue(state.settingsConfig, spec.key) : undefined,
          key.name === "right" ? 1 : -1,
        );
        if (value !== undefined) void saveSetting(state.settingsSelection, value);
        else {
          state.notice = `${spec.label} uses Enter for text entry.`;
          refresh();
        }
        return;
      }
      if (isEnterKey(key.name)) {
        if (spec.kind === "choice") {
          const value = cycleSetting(
            spec,
            state.settingsConfig ? settingValue(state.settingsConfig, spec.key) : undefined,
            1,
          );
          if (value !== undefined) void saveSetting(state.settingsSelection, value);
        } else {
          state.settingsEditor = {
            index: state.settingsSelection,
            buffer: initialEditorValue(
              spec,
              state.settingsConfig ? settingValue(state.settingsConfig, spec.key) : undefined,
            ),
          };
          state.notice = `Editing ${spec.label}. Enter saves; Escape cancels.`;
          refresh();
        }
        return;
      }
    }
    if (state.view === "Models") {
      if (key.name === "up" || key.name === "k") {
        state.modelSelection = Math.max(0, state.modelSelection - 1);
        delete state.modelActionArmed;
        refresh();
        return;
      }
      if (key.name === "down" || key.name === "j") {
        state.modelSelection = Math.min(Math.max(0, (state.models?.length ?? 1) - 1), state.modelSelection + 1);
        delete state.modelActionArmed;
        refresh();
        return;
      }
    }
    const maximumScroll = Math.max(0, state.content.split("\n").length - 2);
    if (key.name === "up" || key.name === "k") { state.scrollOffset = Math.max(0, state.scrollOffset - 1); refresh(); return; }
    if (key.name === "down" || key.name === "j") { state.scrollOffset = Math.min(maximumScroll, state.scrollOffset + 1); refresh(); return; }
    if (["pageup", "pageUp", "page_up"].includes(key.name)) { state.scrollOffset = Math.max(0, state.scrollOffset - 4); refresh(); return; }
    if (["pagedown", "pageDown", "page_down"].includes(key.name)) { state.scrollOffset = Math.min(maximumScroll, state.scrollOffset + 4); refresh(); return; }
    if (key.name === "home") { state.scrollOffset = 0; refresh(); return; }
    const phase = capturePhase(state.status);
    if (key.name === "r" && state.status.model_installing !== true && (phase === "capturing" || state.status.capture_available !== false)) void perform("record.toggle");
    if (key.name === "c" && !["idle", "failed"].includes(phase)) void perform("record.cancel");
    if (state.view === "History" && key.name === "l") void hydrateView("History");
    if (state.view === "Modes" && key.name === "m") {
      const modes = ["raw", "clean", "code"];
      const current = String(state.status.mode ?? "raw");
      const next = modes[(modes.indexOf(current) + 1) % modes.length] ?? "raw";
      void perform("modes.select", { name: next });
    }
    if (state.view === "Words" && key.name === "v") void hydrateView("Words");
    if (state.view === "Models" && ["i", "v", "s", "r"].includes(key.name)) {
      const model = state.models?.[state.modelSelection];
      const name = String(model?.name ?? "balanced");
      const unsafe = state.busy || state.status.model_installing === true || !["idle", "failed"].includes(phase);
      if (unsafe) {
        state.notice = phase === "capturing"
          ? "Model actions are disabled while recording. Stop or cancel capture first."
          : "Model actions are disabled while another capture, transcription, or download is active.";
        refresh();
        return;
      }
      if (key.name === "v") {
        void perform("models.verify", { name });
        return;
      }
      if (key.name === "s") {
        void perform("models.select", { name });
        return;
      }
      const action = key.name === "i" ? "install" : "remove";
      if (state.modelActionArmed?.action !== action || state.modelActionArmed.name !== name) {
        state.modelActionArmed = { action, name };
        state.notice = action === "install"
          ? `Confirm ${name}'s pinned source, MIT license, exact size, and SHA-256 shown above. Press I again.`
          : `Remove the verified ${name} artifact from OpenWhisper's private model directory? Press R again.`;
        refresh();
      } else {
        delete state.modelActionArmed;
        void perform(`models.${action}`, action === "install" ? { name, yes: true } : { name });
      }
    }
    if (state.view === "Doctor" && key.name === "d") void hydrateView("Doctor");
    if (state.view === "Logs" && key.name === "l") void hydrateView("Logs");
  });

  renderer.keyInput.on("paste", (event) => {
    if (state.view !== "Settings" || !state.settingsEditor) return;
    appendEditorText(new TextDecoder().decode(event.bytes));
  });

  return {
    root,
    onQuit(listener: () => void) {
      quitListeners.add(listener);
    },
    updateStatus(status: Record<string, unknown>) {
      const previousPhase = capturePhase(state.status);
      state.status = status;
      const nextPhase = capturePhase(status);
      if (nextPhase === "capturing" && previousPhase !== "capturing") delete state.lastResult;
      if (nextPhase !== "capturing") delete state.audioLevel;
      const failure = captureFailure(status);
      if (failure) state.notice = `Capture failed · ${failure}`;
      if (state.view === "Capture") {
        state.content = viewContent(state.view, status, state.lastResult, state.audioLevel);
        refresh();
      } else {
        void hydrateView(state.view);
      }
    },
    setNotice(notice: string) {
      state.notice = notice;
      refresh();
    },
    updateModelProgress(progress: Record<string, unknown>) {
      const downloaded = Number(progress.downloaded_bytes ?? 0);
      const total = Number(progress.total_bytes ?? 0);
      const percent = total > 0 ? Math.min(100, Math.floor(downloaded * 100 / total)) : 0;
      state.status = { ...state.status, model_installing: true, model_download: progress };
      state.notice = `Downloading ${String(progress.name ?? "balanced")}: ${formatBytes(downloaded)} / ${formatBytes(total)} (${percent}%)`;
      if (state.view === "Models") state.content = modelProgressContent(progress);
      refresh();
    },
    updateAudioLevel(level: Record<string, unknown>) {
      if (capturePhase(state.status) !== "capturing") return;
      state.audioLevel = level;
      if (state.view === "Capture") state.content = viewContent("Capture", state.status, undefined, level);
      refresh();
    },
    updateTranscriptionPreview(event: Record<string, unknown>) {
      const streaming = asRecord(state.status.streaming as JsonValue);
      state.status = { ...state.status, streaming: { ...streaming, preview: String(event.text ?? ""), latency_ms: Number(event.latency_ms ?? 0) } };
      if (state.view === "Capture") state.content = viewContent("Capture", state.status, undefined, state.audioLevel);
      refresh();
    },
    updateTranscriptionCommit(event: Record<string, unknown>) {
      const streaming = asRecord(state.status.streaming as JsonValue);
      state.status = { ...state.status, streaming: { ...streaming, committed: String(event.committed ?? "") } };
      if (state.view === "Capture") state.content = viewContent("Capture", state.status, undefined, state.audioLevel);
      refresh();
    },
    updateInsertionState(event: Record<string, unknown>) {
      const streaming = asRecord(state.status.streaming as JsonValue);
      state.status = { ...state.status, streaming: { ...streaming, insertion_status: String(event.status ?? "not_requested"), inserted_bytes: Number(event.inserted_bytes ?? streaming.inserted_bytes ?? 0) } };
      if (event.status === "suspended") state.notice = `Live insertion suspended · ${String(event.reason ?? "target safety check failed")}`;
      if (state.view === "Capture") state.content = viewContent("Capture", state.status, undefined, state.audioLevel);
      refresh();
    },
    showResult(result: Record<string, unknown>) {
      state.lastResult = result;
      state.content = viewContent("Capture", state.status, result);
      state.scrollOffset = 0;
      state.view = "Capture";
      refresh();
    },
    refreshLayout() { refresh(); },
    selectView(view: ViewName) {
      delete state.settingsEditor;
      state.view = view;
      state.content = viewContent(view, state.status);
      refresh();
      void hydrateView(view);
    },
  };
}

function visibleContent(content: string, offset: number, height: number): string {
  const lines = content.split("\n");
  const limit = Math.max(4, height - 12);
  return lines.slice(offset, offset + limit).join("\n");
}

function capturePhase(status: Record<string, unknown>): string {
  const capture = status.capture;
  return capture && typeof capture === "object" && "phase" in capture ? String(capture.phase) : "idle";
}

function statusLine(status: Record<string, unknown>): string {
  const phase = capturePhase(status).toUpperCase();
  const marker = phase === "CAPTURING" ? "[REC]"
    : ["TRANSCRIBING", "PROCESSING", "DELIVERING"].includes(phase) ? "[WORKING]"
    : status.model_installing === true ? "[WORKING]"
    : phase === "FAILED" ? "[DEGRADED]"
    : status.capture_available === false ? "[BLOCKED]" : "[READY]";
  return `${marker}  OpenWhisper ${String(status.version ?? "1.0")}   daemon ${String(status.daemon ?? "unknown")}   mode ${String(status.mode ?? "raw")}   language ${String(status.language ?? "auto")}   privacy ${status.local_only === false ? "cloud allowed" : "local only"}`;
}

function navLine(active: ViewName, width: number): string {
  const compact = ["Capt", "Hist", "Modes", "Words", "Modl", "Sett", "Doct", "Logs"];
  return VIEW_NAMES.map((name, index) => {
    const label = width < 85 ? compact[index] : name;
    return `${index + 1} ${name === active ? `[${label}]` : label}`;
  }).join("   ");
}

function viewContent(view: ViewName, status: Record<string, unknown>, result?: Record<string, unknown>, audioLevel?: Record<string, unknown>, compactCapture = false): string {
  const phase = capturePhase(status);
  const streaming = asRecord(status.streaming as JsonValue);
  const committed = String(streaming.committed ?? "");
  const preview = String(streaming.preview ?? "");
  const actualBackend = String(asRecord(streaming.backend as JsonValue).actual ?? status.actual_backend ?? "unknown");
  const insertion = String(streaming.insertion_status ?? "not_requested");
  const content: Record<ViewName, string> = {
    Capture: phase === "capturing"
      ? compactCapture
        ? audioMeter(audioLevel, true)
        : `Recording is active\n\n${audioMeter(audioLevel)}\n\nCommitted  ${committed || "—"}\nPreview    ${preview || "—"}\n\nModel: ${String(status.model ?? "balanced")} · Backend: ${actualBackend}\nLatency: ${Number(streaming.latency_ms ?? 0)} ms · Insertion: ${insertion}\nMicrophone: ${String(status.audio_backend ?? "system default")} · Elapsed: ${elapsed(status)}\n\nTUI capture is preview-only. R flushes the remaining words; C cancels and deletes audio.`
      : ["transcribing", "processing", "delivering"].includes(phase)
        ? `Dictation is working\n\nMicrophone: ${String(status.audio_backend ?? "system default")}\nModel: ${String(status.model ?? "balanced")}\nPhase: ${phase}\n\nC cancels this generation and removes its temporary audio.`
      : phase === "failed"
        ? failureContent(status, compactCapture)
      : result && typeof result.final_text === "string"
        ? `Final transcript\n\n${result.final_text}\n\nModel: ${String(status.model ?? "balanced")} · Backend: ${String(result.actual_backend ?? status.actual_backend ?? "unknown")}\nInsertion: ${String(result.insertion_status ?? result.insertion_method ?? "not_requested")} · ${Number(result.inserted_bytes ?? 0)} bytes\nClipboard: ${result.copied === true ? "copied" : "not copied — use the selectable text above"}\nHistory ID: ${String(result.history_id ?? "not saved")} · Duration: ${String(result.duration_ms ?? 0)} ms`
      : status.capture_available === false
        ? "Microphone capture is not available in this build\n\nRun `openwhisper doctor --json` to see the blocked adapter and its fallback. No microphone is opened when you press R.\n\nLogical Unicode remains exact: شغّل cargo test من فضلك"
        : "Ready for dictation\n\nPress R for preview-only TUI recording. Global Alt+O requests daemon-owned safe live insertion into the retained target window and keeps working after this terminal closes.\n\nArabic stays in logical Unicode order: شغّل cargo test من فضلك",
    History: "Searchable history\n\nUse `openwhisper history list` or `openwhisper history search <query>`. History defaults to 30 days and can be disabled. Captured audio is never retained after completion or cancellation.",
    Modes: "Raw · Clean · Code\n\nRaw preserves the transcript. Clean applies deterministic cleanup and replacements. Code preserves line structure for developer speech.\n\nSelect with `openwhisper modes select <name>`.",
    Words: "Vocabulary, replacements, and snippets\n\nTeach product names without uploading them. Deterministic replacements run after transcription. Snippets expand only when explicitly invoked.",
    Models: "Local model management\n\nFast, balanced, and accurate are built-in pinned whisper.cpp profiles. Downloads require explicit confirmation and exact size/SHA-256 verification.\n\nBenchmark not run — does not block dictation. Backend choices are Automatic, Vulkan, and CPU.",
    Settings: `Configuration\n\nMode: ${String(status.mode ?? "raw")}\nLanguage: ${String(status.language ?? "auto")}\nPrivacy: ${status.local_only === false ? "cloud providers allowed" : "local only"}\n\nUse the config commands for scriptable changes.`,
    Doctor: "Capability diagnostics\n\nRun `openwhisper doctor --json` for audio, toggle/PTT, insertion, overlay, notifications, secrets, service manager, and accelerator details with actionable fallbacks.",
    Logs: "Privacy-safe service logs\n\nLogs contain state transitions and sanitized failures only. Microphone audio, transcripts, API keys, clipboard contents, and request parameters are prohibited.",
  };
  return content[view];
}

function captureFailure(status: Record<string, unknown>): string | undefined {
  const capture = asRecord(status.capture as JsonValue);
  return capture.phase === "failed" && typeof capture.message === "string" ? capture.message : undefined;
}

function failureContent(status: Record<string, unknown>, compact: boolean): string {
  const message = captureFailure(status) ?? "The recording could not be completed.";
  if (compact) return `Capture failed · ${message}`;
  const recovery = message.includes("no text")
    ? "Recovery: Confirm the meter reaches SIGNAL while you speak, then press R to retry."
    : "Recovery: Press R to retry. If it repeats, open Doctor with 7 for the blocked component.";
  return `Capture failed\nReason: ${message}\nClipboard: unchanged — no transcript was produced.\nHistory: unchanged — failed captures are not saved.\n${recovery}`;
}

function audioMeter(level?: Record<string, unknown>, compact = false): string {
  const dbfs = Math.max(-60, Math.min(0, Number(level?.dbfs ?? -60)));
  const segments = compact ? 10 : 18;
  const active = Math.max(0, Math.min(segments, Math.round((dbfs + 60) / 60 * segments)));
  const meter = `${"█".repeat(active)}${"░".repeat(segments - active)}`;
  const bytes = Math.max(0, Number(level?.bytes_captured ?? 0));
  const clipping = level?.clipping === true;
  const signal = level?.signal === true;
  if (compact) {
    const state = clipping ? "CLIP" : signal ? "SIGNAL" : bytes > 0 ? "LIVE" : "OPEN";
    return `Recording is active · ${dbfs.toFixed(1)} dBFS · ${state}`;
  }
  const state = clipping ? "CLIPPING — lower the input level" : signal ? "Signal detected" : bytes > 0 ? "Stream active · waiting for speech" : "Opening microphone stream";
  return `Input  [${meter}]  ${dbfs.toFixed(1)} dBFS\n${state} · ${formatBytes(bytes)} captured`;
}

function elapsed(status: Record<string, unknown>): string {
  const capture = asRecord(status.capture as JsonValue);
  const started = typeof capture.started_at === "string" ? Date.parse(capture.started_at) : Number.NaN;
  if (!Number.isFinite(started)) return "00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1_000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function actionLine(view: ViewName, status: Record<string, unknown>, width: number, editingSettings = false): string {
  const phase = capturePhase(status);
  const capture = phase === "capturing" ? "[R] stop   [C] cancel"
    : ["transcribing", "processing", "delivering"].includes(phase) ? "[C] cancel"
    : status.model_installing === true ? "record unavailable — model installing"
    : status.capture_available !== false ? "[R] record" : "record unavailable";
  const contextual: Record<ViewName, string> = {
    Capture: "",
    History: "[L] reload",
    Modes: "[M] next mode",
    Words: "[V] reload vocabulary",
    Models: status.model_installing === true ? "model install in progress" : "[↑↓] choose  [I] install  [V] verify  [S] select  [R] remove",
    Settings: "[↑↓] select   [←→] change   [Enter] edit",
    Doctor: "[D] refresh",
    Logs: "[L] refresh",
  };
  if (view === "Settings" && editingSettings) {
    return width < 70
      ? "[Enter] save   [Esc] cancel"
      : "[Enter] save   [Esc] cancel   [Backspace] delete";
  }
  if (view === "Settings" && width < 70) return "[↑↓] select   [Enter] change   [Q] quit";
  if (width < 70) return `${capture}   [Q] quit`;
  return `${capture}   ${contextual[view]}   [1–8] views   [Q] quit`.replace(/\s{5,}/g, "   ");
}

async function loadView(view: ViewName, request: Request, status: Record<string, unknown>): Promise<string> {
  if (view === "Capture") return viewContent(view, status);
  if (view === "History") return formatRows("Recent transcripts", await request("history.list", { limit: 20 }), "No transcript history yet.");
  if (view === "Modes") return formatRows("Available modes", await request("modes.list"), "No modes reported.");
  if (view === "Words") {
    const [vocabulary, snippets] = await Promise.all([request("vocab.list"), request("snippets.list")]);
    return `${formatRows("Vocabulary", vocabulary, "No vocabulary terms yet.")}\n\n${formatRows("Snippets", snippets, "No snippets yet.")}`;
  }
  if (view === "Models") {
    const [models, providers] = await Promise.all([request("models.list"), request("providers.list")]);
    return `${formatModels(models)}\n\n${formatRows("Providers", providers, "No providers configured.")}`;
  }
  if (view === "Settings") return formatObject("Configuration", await request("config.list"));
  if (view === "Doctor") return formatDoctor(await request("system.doctor"));
  return formatObject("Privacy-safe service logs", await request("system.logs", { follow: false }));
}

function formatModels(value: JsonValue, selected = 0): string {
  if (!Array.isArray(value) || value.length === 0) return "Models\n\nNo built-in models were reported.";
  return `Models · Up/Down chooses a profile\n\n${value.map((raw, index) => {
    const model = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const state = model.installing === true ? "INSTALLING" : model.installed === true ? "INSTALLED" : "AVAILABLE";
    return `${index === selected ? ">" : " "} ${String(model.name ?? "unknown")} · ${state}${model.selected === true ? " · SELECTED" : ""}\n  ${String(model.artifact_name ?? model.model_id ?? "unknown")} · ${formatBytes(Number(model.size_bytes ?? 0))}\n  verification ${String(model.verification_state ?? (model.installed === true ? "verified" : "missing"))} · trust ${String(model.trust ?? "unknown")} · license ${String(model.license ?? "unknown")}\n  pin ${String(model.pinned_revision ?? "unknown")} · ABI ${String(model.worker_abi ?? "unknown")}\n  SHA-256 ${String(model.sha256 ?? "unknown")}\n  Benchmark ${String(model.benchmark_status ?? "not_run").replaceAll("_", " ")} — does not block dictation`;
  }).join("\n\n")}`;
}

function modelProgressContent(progress: Record<string, unknown>): string {
  const downloaded = Number(progress.downloaded_bytes ?? 0);
  const total = Number(progress.total_bytes ?? 0);
  const percent = total > 0 ? Math.min(100, Math.floor(downloaded * 100 / total)) : 0;
  return `Installing ${String(progress.name ?? "balanced")}\n\n${formatBytes(downloaded)} / ${formatBytes(total)} · ${percent}%\n\nThe daemon is verifying a resumable private download. Capture and duplicate install actions remain disabled until registration completes.\n\nBenchmark: not run — non-blocking.`;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  const amount = value / 1024 ** index;
  return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function formatRows(title: string, value: JsonValue, empty: string): string {
  if (!Array.isArray(value) || value.length === 0) return `${title}\n\n${empty}`;
  return `${title}\n\n${value.map((item) => `• ${compactValue(item)}`).join("\n")}`;
}

function formatObject(title: string, value: JsonValue): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return `${title}\n\n${compactValue(value)}`;
  return `${title}\n\n${Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${compactValue(item)}`).join("\n")}`;
}

function formatDoctor(value: JsonValue): string {
  const root = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const inference = root.inference && typeof root.inference === "object" && !Array.isArray(root.inference) ? root.inference : {};
  const capabilities = root.capabilities && typeof root.capabilities === "object" && !Array.isArray(root.capabilities) ? root.capabilities : {};
  const rows = Object.entries(capabilities).map(([name, raw]) => {
    const capability = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const state = capability.available === true ? "READY" : "BLOCKED";
    return `${name.replaceAll("_", " ")}\n  ${state} · ${String(capability.backend ?? "none")}\n  ${String(capability.detail ?? "No detail reported.")}\n  Recovery: ${String(capability.fallback ?? "No action required.")}`;
  });
  const backend = `Inference\n  requested ${String(inference.requested_backend ?? "unknown")} · actual ${String(inference.actual_backend ?? "unknown")}\n  device ${String(inference.gpu_device ?? "none")} · fallback ${String(inference.fallback_reason ?? "none")}\n  profile ${String(inference.selected_profile ?? "unknown")} · benchmark ${String(inference.benchmark_status ?? "not_run")}`;
  return `Capability diagnostics\n\n${backend}\n\n${rows.length > 0 ? rows.join("\n\n") : "No capability data was reported."}`;
}

function compactValue(value: JsonValue): string {
  if (value === null) return "none";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return value.map(compactValue).join(", ");
  const preferred = ["id", "name", "text", "description", "created_at", "available", "enabled"];
  const entries = Object.entries(value);
  const ordered = [...preferred.flatMap((key) => entries.filter(([entry]) => entry === key)), ...entries.filter(([key]) => !preferred.includes(key))];
  return ordered.map(([key, item]) => `${key}=${compactValue(item)}`).join("  ");
}

function asRecord(value: JsonValue): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function shortResult(value: JsonValue): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if ("state" in value) return `Capture state: ${String(value.state)}`;
    if ("cancelled" in value) return "Capture cancelled; temporary audio removed.";
  }
  return "Action completed.";
}

function isEnterKey(name: string): boolean {
  return name === "return" || name === "enter" || name === "linefeed";
}
