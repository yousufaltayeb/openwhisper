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
  modelInstallArmed: boolean;
  lastResult?: Record<string, unknown>;
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
        else scene.setNotice(event.event.replaceAll(".", " "));
        if (event.event === "result.available") {
          const data = asRecord(event.data);
          if (typeof data.session_id === "string") void client.request("record.result", { session_id: data.session_id }).then((result) => scene.showResult(asRecord(result))).catch((error) => scene.setNotice(error instanceof Error ? error.message : "Final transcript could not be recovered."));
        }
        void client.request("system.status").then((value) => scene.updateStatus(asRecord(value)));
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
              if (event.type === "event") scene.setNotice(event.event.replaceAll(".", " "));
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
    content: viewContent("Capture", initialStatus),
    notice: "Connected. Audio never crosses the public IPC boundary.",
    loadGeneration: 0,
    busy: false,
    modelInstallArmed: false,
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
    content: actionLine(state.view, state.status, renderer.width),
    truncate: true,
  });
  stage.add(contentText);
  root.add(statusText);
  root.add(navText);
  root.add(stage);
  root.add(actionsText);
  root.add(noticeText);

  const quitListeners = new Set<() => void>();
  const refresh = () => {
    statusText.content = statusLine(state.status);
    navText.content = navLine(state.view, renderer.width);
    stage.title = ` ${state.view.toUpperCase()} `;
    stage.titleColor = capturePhase(state.status) === "capturing" ? COLOR.record : COLOR.ink;
    contentText.content = visibleContent(state.content, state.scrollOffset, renderer.height);
    noticeText.content = state.notice;
    actionsText.content = actionLine(state.view, state.status, renderer.width);
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

  renderer.keyInput.on("keypress", (key) => {
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
      state.view = selected;
      state.content = viewContent(selected, state.status);
      refresh();
      void hydrateView(selected);
      return;
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
    if (state.view === "Models" && key.name === "i") {
      if (state.busy || state.status.model_installing === true) {
        state.notice = "The balanced model installation is already in progress.";
        refresh();
        return;
      }
      if (!state.modelInstallArmed) {
        state.modelInstallArmed = true;
        state.notice = "Confirm the built-in pinned source, MIT license, 574041195-byte size, and SHA-256 shown above. Benchmark not run is non-blocking. Press I again.";
        refresh();
      } else {
        state.modelInstallArmed = false;
        void perform("models.install", { name: "balanced", yes: true });
      }
    }
    if (state.view === "Settings" && key.name === "p") {
      void perform("config.set", { key: "privacy.local_only", value: state.status.local_only === false });
    }
    if (state.view === "Doctor" && key.name === "d") void hydrateView("Doctor");
    if (state.view === "Logs" && key.name === "l") void hydrateView("Logs");
  });

  return {
    root,
    onQuit(listener: () => void) {
      quitListeners.add(listener);
    },
    updateStatus(status: Record<string, unknown>) {
      state.status = status;
      if (state.view === "Capture") {
        state.content = viewContent(state.view, status, state.lastResult);
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
    showResult(result: Record<string, unknown>) {
      state.lastResult = result;
      state.content = viewContent("Capture", state.status, result);
      state.scrollOffset = 0;
      state.view = "Capture";
      refresh();
    },
    refreshLayout() { refresh(); },
    selectView(view: ViewName) {
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

function viewContent(view: ViewName, status: Record<string, unknown>, result?: Record<string, unknown>): string {
  const phase = capturePhase(status);
  const content: Record<ViewName, string> = {
    Capture: result && typeof result.final_text === "string"
      ? `Final transcript\n\n${result.final_text}\n\nMicrophone: ${String(status.audio_backend ?? "system default")}\nModel: ${String(status.model ?? "balanced")}\nPhase: complete\nClipboard: ${result.copied === true ? "copied" : "not copied — use the selectable text above"}\nHistory ID: ${String(result.history_id ?? "not saved")}\nDuration: ${String(result.duration_ms ?? 0)} ms`
      : phase === "capturing"
      ? `Recording is active\n\nSpeak naturally in العربية and English. The daemon owns capture even if this terminal closes.\n\nMicrophone: ${String(status.audio_backend ?? "system default")}\nModel: ${String(status.model ?? "balanced")}\nElapsed: ${elapsed(status)}\nPhase: capturing\n\nR stops and transcribes. C cancels and deletes captured audio.`
      : ["transcribing", "processing", "delivering"].includes(phase)
        ? `Dictation is working\n\nMicrophone: ${String(status.audio_backend ?? "system default")}\nModel: ${String(status.model ?? "balanced")}\nPhase: ${phase}\n\nC cancels this generation and removes its temporary audio.`
      : status.capture_available === false
        ? "Microphone capture is not available in this build\n\nRun `openwhisper doctor --json` to see the blocked adapter and its fallback. No microphone is opened when you press R.\n\nLogical Unicode remains exact: شغّل cargo test من فضلك"
        : "Ready for dictation\n\nPress R to begin. OpenWhisper retains the target window at capture start and inserts only when it is still safe. Otherwise it copies and notifies.\n\nArabic stays in logical Unicode order: شغّل cargo test من فضلك",
    History: "Searchable history\n\nUse `openwhisper history list` or `openwhisper history search <query>`. History defaults to 30 days and can be disabled. Captured audio is never retained after completion or cancellation.",
    Modes: "Raw · Clean · Code\n\nRaw preserves the transcript. Clean applies deterministic cleanup and replacements. Code preserves line structure for developer speech.\n\nSelect with `openwhisper modes select <name>`.",
    Words: "Vocabulary, replacements, and snippets\n\nTeach product names without uploading them. Deterministic replacements run after transcription. Snippets expand only when explicitly invoked.",
    Models: "Local model management\n\nThe balanced large-v3-turbo Q5 model is trusted by a built-in pinned source, exact size, and SHA-256. It is not bundled and downloads only after explicit confirmation.\n\nBenchmark: not run — this notice is non-blocking. CPU is the guaranteed backend.",
    Settings: `Configuration\n\nMode: ${String(status.mode ?? "raw")}\nLanguage: ${String(status.language ?? "auto")}\nPrivacy: ${status.local_only === false ? "cloud providers allowed" : "local only"}\n\nUse the config commands for scriptable changes.`,
    Doctor: "Capability diagnostics\n\nRun `openwhisper doctor --json` for audio, toggle/PTT, insertion, overlay, notifications, secrets, service manager, and accelerator details with actionable fallbacks.",
    Logs: "Privacy-safe service logs\n\nLogs contain state transitions and sanitized failures only. Microphone audio, transcripts, API keys, clipboard contents, and request parameters are prohibited.",
  };
  return content[view];
}

function elapsed(status: Record<string, unknown>): string {
  const capture = asRecord(status.capture as JsonValue);
  const started = typeof capture.started_at === "string" ? Date.parse(capture.started_at) : Number.NaN;
  if (!Number.isFinite(started)) return "00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1_000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function actionLine(view: ViewName, status: Record<string, unknown>, width: number): string {
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
    Models: status.model_installing === true ? "model install in progress" : "[I] install / confirm",
    Settings: "[P] toggle local-only",
    Doctor: "[D] refresh",
    Logs: "[L] refresh",
  };
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

function formatModels(value: JsonValue): string {
  if (!Array.isArray(value) || value.length === 0) return "Models\n\nNo built-in models were reported.";
  return `Models\n\n${value.map((raw) => {
    const model = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const state = model.installing === true ? "INSTALLING" : model.installed === true ? "INSTALLED" : "AVAILABLE";
    return `${String(model.name ?? "unknown")} · ${state}${model.selected === true ? " · SELECTED" : ""}\n  ${String(model.model_id ?? "unknown")} · ${formatBytes(Number(model.size_bytes ?? 0))}\n  trust ${String(model.trust ?? "unknown")} · license ${String(model.license ?? "unknown")} · ABI ${String(model.worker_abi ?? "unknown")}\n  SHA-256 ${String(model.sha256 ?? "unknown")}\n  benchmark ${String(model.benchmark_status ?? "unknown")} — non-blocking`;
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
  const capabilities = root.capabilities && typeof root.capabilities === "object" && !Array.isArray(root.capabilities) ? root.capabilities : {};
  const rows = Object.entries(capabilities).map(([name, raw]) => {
    const capability = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const state = capability.available === true ? "READY" : "BLOCKED";
    return `${name.replaceAll("_", " ")}\n  ${state} · ${String(capability.backend ?? "none")}\n  ${String(capability.detail ?? "No detail reported.")}\n  Recovery: ${String(capability.fallback ?? "No action required.")}`;
  });
  return `Capability diagnostics\n\n${rows.length > 0 ? rows.join("\n\n") : "No capability data was reported."}`;
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
