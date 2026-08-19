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
import { BoxRenderable, TextRenderable, createCliRenderer, type CliRenderer } from "@opentui/core";

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
  try {
    const status = asRecord(await client.request("system.status"));
    const scene = buildTuiScene(renderer, status, (method, params) => client.request(method, params));
    renderer.root.add(scene.root);
    const unsubscribe = client.subscribe(0, (event) => {
      if (event.type === "snapshot") scene.updateStatus(asRecord(event.state));
      if (event.type === "event") {
        scene.setNotice(event.event.replaceAll(".", " "));
        void client.request("system.status").then((value) => scene.updateStatus(asRecord(value)));
      }
    });
    await new Promise<void>((resolve) => {
      scene.onQuit(resolve);
      const removeDisconnect = client.onDisconnect(() => {
        scene.setNotice("Daemon disconnected. Restoring the terminal.");
        removeDisconnect();
        resolve();
      });
    });
    unsubscribe();
  } finally {
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
    contentText.content = state.content;
    noticeText.content = state.notice;
    actionsText.content = actionLine(state.view, state.status, renderer.width);
  };

  const setFailure = (error: unknown) => {
    state.notice = error instanceof Error ? error.message : "The action failed.";
    refresh();
  };

  const hydrateView = async (view: ViewName) => {
    const generation = ++state.loadGeneration;
    if (view !== "Capture") {
      state.content = `Loading ${view.toLowerCase()}…`;
      refresh();
    }
    try {
      const content = await loadView(view, onRequest, state.status);
      if (generation !== state.loadGeneration || view !== state.view) return;
      state.content = content;
      refresh();
    } catch (error) {
      if (generation !== state.loadGeneration || view !== state.view) return;
      setFailure(error);
      state.content = viewContent(view, state.status);
      refresh();
    }
  };

  const perform = async (method: string, params: JsonValue = null) => {
    try {
      const result = await onRequest(method, params);
      state.notice = shortResult(result);
      const status = await onRequest("system.status");
      state.status = asRecord(status);
      state.content = viewContent(state.view, state.status);
      refresh();
      if (state.view !== "Capture") await hydrateView(state.view);
    } catch (error) {
      setFailure(error);
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
    if (key.name === "r") void perform("record.toggle");
    if (key.name === "c") void perform("record.cancel");
    if (state.view === "History" && key.name === "l") void hydrateView("History");
    if (state.view === "Modes" && key.name === "m") {
      const modes = ["raw", "clean", "code"];
      const current = String(state.status.mode ?? "raw");
      const next = modes[(modes.indexOf(current) + 1) % modes.length] ?? "raw";
      void perform("modes.select", { name: next });
    }
    if (state.view === "Words" && key.name === "v") void hydrateView("Words");
    if (state.view === "Models" && key.name === "i") {
      state.notice = "Install remains blocked until the signed catalog and benchmark gates pass.";
      refresh();
      void hydrateView("Models");
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
        state.content = viewContent(state.view, status);
        refresh();
      } else {
        void hydrateView(state.view);
      }
    },
    setNotice(notice: string) {
      state.notice = notice;
      refresh();
    },
    selectView(view: ViewName) {
      state.view = view;
      state.content = viewContent(view, state.status);
      refresh();
      void hydrateView(view);
    },
  };
}

function capturePhase(status: Record<string, unknown>): string {
  const capture = status.capture;
  return capture && typeof capture === "object" && "phase" in capture ? String(capture.phase) : "idle";
}

function statusLine(status: Record<string, unknown>): string {
  const phase = capturePhase(status).toUpperCase();
  const marker = phase === "CAPTURING" ? "[REC]" : status.capture_available === false ? "[BLOCKED]" : "[READY]";
  return `${marker}  OpenWhisper ${String(status.version ?? "1.0")}   daemon ${String(status.daemon ?? "unknown")}   mode ${String(status.mode ?? "raw")}   language ${String(status.language ?? "auto")}   privacy ${status.local_only === false ? "cloud allowed" : "local only"}`;
}

function navLine(active: ViewName, width: number): string {
  const compact = ["Capt", "Hist", "Modes", "Words", "Modl", "Sett", "Doct", "Logs"];
  return VIEW_NAMES.map((name, index) => {
    const label = width < 85 ? compact[index] : name;
    return `${index + 1} ${name === active ? `[${label}]` : label}`;
  }).join("   ");
}

function viewContent(view: ViewName, status: Record<string, unknown>): string {
  const phase = capturePhase(status);
  const content: Record<ViewName, string> = {
    Capture: phase === "capturing"
      ? "Recording is active\n\nSpeak naturally in العربية and English. The daemon owns capture even if this terminal closes.\n\nR stops and transcribes. C cancels and deletes captured audio."
      : status.capture_available === false
        ? "Microphone capture is not available in this build\n\nRun `openwhisper doctor --json` to see the blocked adapter and its fallback. No microphone is opened when you press R.\n\nLogical Unicode remains exact: شغّل cargo test من فضلك"
        : "Ready for dictation\n\nPress R to begin. OpenWhisper retains the target window at capture start and inserts only when it is still safe. Otherwise it copies and notifies.\n\nArabic stays in logical Unicode order: شغّل cargo test من فضلك",
    History: "Searchable history\n\nUse `openwhisper history list` or `openwhisper history search <query>`. History defaults to 30 days and can be disabled. Captured audio is never retained after completion or cancellation.",
    Modes: "Raw · Clean · Code\n\nRaw preserves the transcript. Clean applies deterministic cleanup and replacements. Code preserves line structure for developer speech.\n\nSelect with `openwhisper modes select <name>`.",
    Words: "Vocabulary, replacements, and snippets\n\nTeach product names without uploading them. Deterministic replacements run after transcription. Snippets expand only when explicitly invoked.",
    Models: "No model is bundled\n\nThe balanced large-v3-turbo Q5 candidate remains blocked until its signed catalog, license, Arabic benchmark, and latency gates pass. CPU is the guaranteed backend.",
    Settings: `Configuration\n\nMode: ${String(status.mode ?? "raw")}\nLanguage: ${String(status.language ?? "auto")}\nPrivacy: ${status.local_only === false ? "cloud providers allowed" : "local only"}\n\nUse the config commands for scriptable changes.`,
    Doctor: "Capability diagnostics\n\nRun `openwhisper doctor --json` for audio, toggle/PTT, insertion, overlay, notifications, secrets, service manager, and accelerator details with actionable fallbacks.",
    Logs: "Privacy-safe service logs\n\nLogs contain state transitions and sanitized failures only. Microphone audio, transcripts, API keys, clipboard contents, and request parameters are prohibited.",
  };
  return content[view];
}

function actionLine(view: ViewName, status: Record<string, unknown>, width: number): string {
  const capture = capturePhase(status) === "capturing" ? "[R] stop   [C] cancel" : "[R] record   [C] cancel";
  const contextual: Record<ViewName, string> = {
    Capture: "",
    History: "[L] reload",
    Modes: "[M] next mode",
    Words: "[V] reload vocabulary",
    Models: "[I] inspect release gate",
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
    return `${formatRows("Models", models, "No models installed.")}\n\n${formatRows("Providers", providers, "No providers configured.")}`;
  }
  if (view === "Settings") return formatObject("Configuration", await request("config.list"));
  if (view === "Doctor") return formatObject("Capability diagnostics", await request("system.doctor"));
  return formatObject("Privacy-safe service logs", await request("system.logs", { follow: false }));
}

function formatRows(title: string, value: JsonValue, empty: string): string {
  if (!Array.isArray(value) || value.length === 0) return `${title}\n\n${empty}`;
  return `${title}\n\n${value.map((item) => `• ${compactValue(item)}`).join("\n")}`;
}

function formatObject(title: string, value: JsonValue): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return `${title}\n\n${compactValue(value)}`;
  return `${title}\n\n${Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${compactValue(item)}`).join("\n")}`;
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
