import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { bootstrapFixture, event, FakeEngineAdapter } from "./test/fixtures";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-reduced-motion");
});

describe("Capture surface", () => {
  it("boots into a complete ready state and starts dictation", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);

    expect(await screen.findByRole("heading", { name: "Capture" })).toBeInTheDocument();
    expect(screen.getByText("Faster Whisper · large-v3-turbo")).toBeInTheDocument();
    expect(screen.getByText("System default")).toBeInTheDocument();
    expect(screen.getByText("Transcript state stays in memory")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start dictation" }));
    expect(adapter.calls[adapter.calls.length - 1]).toEqual({
      method: "dictation.start",
      params: {},
    });
  });

  it("renders mixed Arabic content with automatic direction", async () => {
    const adapter = new FakeEngineAdapter();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(
        event(1, "dictation.state", { state: "recording", sessionId: "arabic-session" }),
      );
      adapter.emit(
        event(2, "dictation.partial", {
          text: "مرحبا OpenWhisper 123",
          sessionId: "arabic-session",
        }),
      );
    });

    const transcript = screen.getByText("مرحبا OpenWhisper 123");
    expect(transcript).toHaveAttribute("dir", "auto");
    expect(screen.getByRole("button", { name: "Stop dictation" })).toBeEnabled();
  });

  it("reports clipboard fallback as a successful copied outcome", async () => {
    const adapter = new FakeEngineAdapter();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(event(1, "dictation.state", { state: "recording", sessionId: "copied" }));
      adapter.emit(event(2, "dictation.state", { state: "completed", sessionId: "copied" }));
      adapter.emit(
        event(3, "notice", {
          level: "info",
          message: "Transcript copied. Paste it in the target application.",
        }),
      );
      adapter.emit(
        event(4, "dictation.completed", {
          text: "Copied result",
          inserted: false,
          insertionMethod: "clipboard",
          sessionId: "copied",
        }),
      );
    });

    expect(screen.getByText("Copied", { exact: true })).toBeInTheDocument();
    expect(
      screen.getByText("Transcript copied. Paste it in the target application."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Direct insertion is unavailable/)).not.toBeInTheDocument();
  });

  it("opens the command drawer from Ctrl+K and restores a dismissible workflow", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole("dialog", { name: "OpenWhisper commands" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close commands" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "OpenWhisper commands" })).not.toBeInTheDocument(),
    );
  });

  it("persists theme through the engine adapter rather than localStorage", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.click(screen.getByRole("button", { name: /Theme: system/ }));
    expect(adapter.calls[adapter.calls.length - 1]).toEqual({
      method: "settings.update",
      params: { changes: { theme: "light" } },
    });
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("shows fatal recovery instead of silently restarting", async () => {
    const adapter = new FakeEngineAdapter();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(event(1, "engine.fatal", { message: "The engine stopped safely." }));
    });

    expect(screen.getByRole("heading", { name: "Capture stopped safely" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart OpenWhisper" })).toBeInTheDocument();
  });

  it("completes privacy, microphone, shortcut, and local-provider onboarding", async () => {
    const adapter = new FakeEngineAdapter({
      ...bootstrapFixture,
      firstRun: true,
      settings: { ...bootstrapFixture.settings, onboardingCompleted: false },
    });
    const user = userEvent.setup();
    render(<App adapter={adapter} />);

    expect(await screen.findByRole("dialog", { name: "Set up OpenWhisper" })).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /understand what crosses/i }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Test microphone" }));
    expect(await screen.findByText("The test microphone opened successfully.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Check shortcut status" }));
    expect(await screen.findByText(/Shortcut permission is active/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Local, private transcription.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Open Capture" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Set up OpenWhisper" })).not.toBeInTheDocument(),
    );
    expect(adapter.calls).toContainEqual({
      method: "settings.update",
      params: { changes: { onboardingCompleted: true } },
    });
  });

  it("gates capture when the selected provider is unavailable", async () => {
    const adapter = new FakeEngineAdapter({
      ...bootstrapFixture,
      providers: [
        {
          ...bootstrapFixture.providers[0]!,
          available: false,
          unavailableReason: "The local runtime is missing.",
        },
      ],
    });
    render(<App adapter={adapter} />);

    expect(await screen.findByRole("button", { name: "Start dictation" })).toBeDisabled();
    expect(screen.getAllByText("The local runtime is missing.").length).toBeGreaterThan(0);
  });

  it("shows elapsed capture time and exposes peak level to assistive technology", async () => {
    const adapter = new FakeEngineAdapter();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(event(1, "dictation.state", { state: "recording", sessionId: "timed" }));
      adapter.emit(
        event(2, "dictation.audioLevel", {
          rms: 0.5,
          peak: 0.92,
          elapsed: 12.2,
          sessionId: "timed",
        }),
      );
    });

    expect(screen.getByText("00:12")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Microphone input level" })).toHaveAttribute(
      "aria-valuetext",
      expect.stringContaining("clipping risk"),
    );
    const segments = document.querySelectorAll("[data-active]");
    expect(segments[0]).toHaveAttribute("data-active", "false");
    expect(segments[segments.length - 1]).toHaveAttribute("data-active", "true");
  });

  it("explains first-run local model preparation instead of appearing stuck", async () => {
    const adapter = new FakeEngineAdapter();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(event(1, "dictation.state", { state: "recording", sessionId: "model" }));
      adapter.emit(event(2, "dictation.state", { state: "processing", sessionId: "model" }));
      adapter.emit(event(3, "provider.progress", { stage: "loading_model" }));
    });

    expect(screen.getAllByText("Loading local model").length).toBeGreaterThan(0);
    expect(screen.getByText(/first run can take longer/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("stages output changes and asks before discarding them", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.click(screen.getByRole("button", { name: "Open settings" }));
    expect(screen.getByRole("dialog", { name: "OpenWhisper settings" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("radio").find((radio) => radio.parentElement?.textContent?.startsWith("Copy"))!);
    await user.click(screen.getByRole("button", { name: "Close settings" }));

    expect(screen.getByRole("dialog", { name: "Discard settings changes" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("dialog", { name: "OpenWhisper settings" })).toBeInTheDocument();
    expect(adapter.calls.some((call) => call.method === "settings.update")).toBe(false);
  });

  it("saves final delivery independently from type-while-speaking", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(screen.getAllByRole("radio").find((radio) => radio.parentElement?.textContent?.startsWith("Insert + copy"))!);
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "OpenWhisper settings" })).not.toBeInTheDocument());
    expect(adapter.calls).toContainEqual(
      expect.objectContaining({
        method: "settings.update",
        params: expect.objectContaining({ changes: expect.objectContaining({ outputMode: "both", liveInsertion: false }) }),
      }),
    );
  });

  it("keeps Settings read-only during an active dictation", async () => {
    const adapter = new FakeEngineAdapter();
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    act(() => {
      adapter.emit(event(1, "dictation.state", { state: "recording", sessionId: "settings-read-only" }));
    });
    await user.click(screen.getByRole("button", { name: "Open settings" }));

    expect(screen.getByText(/Settings are read-only while dictation is active/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(screen.getAllByRole("radio").find((radio) => radio.parentElement?.textContent?.startsWith("Copy"))).toBeDisabled();
  });

  it("keeps a saved accelerator change and offers restart recovery", async () => {
    const adapter = new FakeEngineAdapter(
      {
        ...bootstrapFixture,
        compute: {
          active: "auto",
          automaticBackend: "cpu",
          options: [
            { target: "auto", available: true, backend: "cpu", reason: null, supportedComputeTypes: ["auto"] },
            { target: "cpu", available: true, backend: "cpu", reason: null, supportedComputeTypes: ["int8"] },
            { target: "nvidia", available: true, backend: "cuda", reason: null, supportedComputeTypes: ["float16"] },
            { target: "amd", available: false, backend: null, reason: "ROCm is unavailable.", supportedComputeTypes: [] },
          ],
        },
      },
      1,
    );
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(screen.getByRole("radio", { name: /NVIDIA GPU/i }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText(/Settings were saved, but the engine could not restart/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry restart" })).toBeEnabled();
    expect(adapter.calls).toContainEqual(
      expect.objectContaining({
        method: "settings.update",
        params: expect.objectContaining({ changes: expect.objectContaining({ device: "nvidia" }) }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Retry restart" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "OpenWhisper settings" })).not.toBeInTheDocument());
    expect(adapter.calls.filter((call) => call.method === "app.restartEngine")).toHaveLength(2);
  });

  it("selects a historical model alias and canonicalizes it on save", async () => {
    const adapter = new FakeEngineAdapter({
      ...bootstrapFixture,
      settings: { ...bootstrapFixture.settings, transcriptionModel: "turbo" },
    });
    const user = userEvent.setup();
    render(<App adapter={adapter} />);
    await screen.findByRole("heading", { name: "Capture" });

    await user.click(screen.getByRole("button", { name: "Open settings" }));
    expect(screen.getByRole("radio", { name: /large-v3-turbo/i })).toBeChecked();
    expect(screen.getByRole("button", { name: "Download tiny" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /^Copy.*complete final transcript/i }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(adapter.calls).toContainEqual(
      expect.objectContaining({
        method: "settings.update",
        params: expect.objectContaining({ changes: expect.objectContaining({ transcriptionModel: "large-v3-turbo" }) }),
      }),
    );
  });
});
