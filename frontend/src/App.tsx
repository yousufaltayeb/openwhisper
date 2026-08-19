import { useEffect, useMemo, useReducer, useState } from "react";

import { CaptureSurface } from "./components/CaptureSurface";
import { CommandDrawer } from "./components/CommandDrawer";
import { Onboarding } from "./components/Onboarding";
import { SettingsDialog } from "./components/Settings";
import { createEngineAdapter } from "./engine/adapter";
import type { BootstrapDto, EngineAdapter } from "./engine/types";
import { initialState, openWhisperReducer } from "./state/openWhisperReducer";
import styles from "./App.module.css";

interface AppProps {
  adapter?: EngineAdapter;
}

export function App({ adapter: providedAdapter }: AppProps) {
  const adapter = useMemo(() => providedAdapter ?? createEngineAdapter(), [providedAdapter]);
  const [state, dispatch] = useReducer(openWhisperReducer, initialState);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;

    async function connect() {
      try {
        unlisten = await adapter.listen((event) => dispatch({ type: "engine.event", event }));
        const bootstrap = await adapter.request<BootstrapDto>("app.bootstrap", {});
        if (!disposed) {
          dispatch({ type: "app.bootstrapped", bootstrap });
        }
      } catch (error) {
        if (!disposed) {
          dispatch({
            type: "app.failed",
            message: error instanceof Error ? error.message : "The engine did not start.",
          });
        }
      }
    }

    void connect();
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [adapter]);

  useEffect(() => {
    document.documentElement.dataset.theme = state.theme;
    document.documentElement.dataset.reducedMotion = String(state.reducedMotion);
  }, [state.reducedMotion, state.theme]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        dispatch({ type: "commands.open" });
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function request<T = unknown>(method: string, params: Record<string, unknown> = {}) {
    try {
      return await adapter.request<T>(method, params);
    } catch {
      dispatch({
        type: "notice.added",
        level: "error",
        message: publicErrorMessage(method),
      });
      throw new Error(publicErrorMessage(method));
    }
  }

  async function dictationRequest(method: "dictation.start" | "dictation.stop" | "dictation.cancel") {
    if (state.pendingAction) return;
    dispatch({ type: "request.started", method });
    try {
      return await request(method);
    } finally {
      dispatch({ type: "request.finished", method });
    }
  }

  async function setTheme(theme: typeof state.theme) {
    dispatch({ type: "theme.changed", theme });
    try {
      await request("settings.update", { changes: { theme } });
    } catch {
      dispatch({ type: "theme.changed", theme: state.theme });
    }
  }

  async function copyTranscript() {
    if (!state.finalTranscript) return;
    try {
      await navigator.clipboard.writeText(state.finalTranscript);
      dispatch({
        type: "notice.added",
        level: "info",
        message: "The preserved transcript was copied. Paste it in the target application.",
      });
    } catch {
      dispatch({
        type: "notice.added",
        level: "error",
        message: "The transcript is preserved here, but clipboard access was not available.",
      });
    }
  }

  async function restartEngine() {
    const bootstrap = await request<BootstrapDto>("app.restartEngine");
    dispatch({ type: "app.bootstrapped", bootstrap });
  }

  const selectedProvider = state.providers.find(
    (provider) => provider.id === state.settings?.transcriptionProvider,
  );

  return (
    <div className={styles.application}>
      <CaptureSurface
        state={state}
        onStart={() => dictationRequest("dictation.start")}
        onStop={() => dictationRequest("dictation.stop")}
        onCancel={() => dictationRequest("dictation.cancel")}
        onCopyTranscript={copyTranscript}
        onDismissNotice={() => dispatch({ type: "notice.dismissed" })}
        onOpenCommands={() => dispatch({ type: "commands.open" })}
        onOpenSettings={() => setSettingsOpen(true)}
        onSetTheme={setTheme}
      />
      <CommandDrawer
        isOpen={state.commandDrawerOpen}
        dictationState={state.dictationState}
        shortcut={state.settings?.shortcut ?? "<alt>+o"}
        isCaptureReady={selectedProvider?.available === true}
        isPending={state.pendingAction !== null}
        onOpenChange={(isOpen) =>
          dispatch({ type: isOpen ? "commands.open" : "commands.close" })
        }
        onStart={() => dictationRequest("dictation.start")}
        onStop={() => dictationRequest("dictation.stop")}
        onCancel={() => dictationRequest("dictation.cancel")}
      />
      {state.settings ? (
        <Onboarding
          isOpen={state.onboardingOpen}
          settings={state.settings}
          providers={state.providers}
          shortcutStatus={state.shortcutStatus}
          request={request}
          onComplete={() => dispatch({ type: "onboarding.completed" })}
        />
      ) : null}
      {state.settings ? (
        <SettingsDialog
          isOpen={settingsOpen}
          settings={state.settings}
          models={state.models}
          compute={state.compute}
          isReadOnly={["recording", "processing", "cleaning", "inserting"].includes(state.dictationState)}
          request={request}
          onOpenChange={setSettingsOpen}
          onSaved={(settings) => dispatch({ type: "settings.updated", settings })}
          onModelsLoaded={(models) => dispatch({ type: "settings.models.loaded", models })}
          onComputeLoaded={(compute) => dispatch({ type: "settings.compute.loaded", compute })}
          onRestartEngine={restartEngine}
        />
      ) : null}
    </div>
  );
}

function publicErrorMessage(method: string) {
  const messages: Record<string, string> = {
    "dictation.start":
      "OpenWhisper could not start capture. Check the microphone and selected provider, then try again.",
    "dictation.stop":
      "OpenWhisper could not stop capture cleanly. The current session is still available to cancel.",
    "dictation.cancel": "OpenWhisper could not cancel the current session.",
    "settings.update": "The preference could not be saved.",
    "app.restartEngine": "OpenWhisper could not restart its engine safely.",
    "models.list": "The speech model catalog could not be loaded.",
    "models.download": "The speech model could not be downloaded.",
    "models.cancel": "The speech model download could not be cancelled.",
    "models.remove": "The speech model could not be removed.",
    "compute.list": "Hardware capabilities could not be loaded.",
    "compute.capabilities": "Hardware capabilities could not be loaded.",
    "compute.probe": "Hardware validation could not complete.",
    "audio.testDevice": "The microphone test could not complete.",
    "diagnostics.run": "Readiness diagnostics could not complete.",
  };
  return messages[method] ?? "The action could not be completed.";
}
