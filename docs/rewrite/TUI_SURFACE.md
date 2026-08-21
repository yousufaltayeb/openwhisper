# OpenTUI surface brief

Mode: Operate.

The TUI serves bilingual developers who need to see capture safety and act
without learning an interface-specific workflow. Its task is to keep daemon and
capture truth visible while exposing recording, history, modes, words/snippets,
models/providers, settings, diagnostics, and logs. Every action has a stable CLI
equivalent.

During capture, the stage reports dBFS, bytes, signal/clipping, provisional and
committed text, selected profile, actual backend, latency, and insertion state.
The meter uses daemon-owned PCM; raw microphone audio never enters client IPC.

The chosen structure is a live operations board: persistent status rail,
numbered single-level view strip, one context stage, notice row, and terse action
dock. It inherits the precision-instrument graphite/chalk system and reserves
recording coral for `[REC]`. Arabic content remains logical Unicode; terminal
shaping is never persisted. Small terminals shorten view labels but retain
status and actions.

Settings is an editor, not a configuration report. Up/Down moves through the
editable fields, Left/Right changes constrained choices, and Enter opens text or
integer entry. Every accepted change is persisted immediately by the daemon and
reported in the notice row; invalid values remain in the editor with an
actionable error, and Escape cancels without writing. The view covers capture,
audio, model runtime, delivery, history, privacy, and platform feedback settings.
Model installation and provider credentials remain in their dedicated guarded
workflows.

Delivery separates final clipboard ownership from daemon-owned live insertion.
Alt+O requests an X11 target; TUI recording stays preview-only. Every committed
delta revalidates the original target. A focus change suspends insertion for the
rest of the session without stopping transcription; the final result is copied.

Models lists fast, balanced, and accurate. Up/Down selects; I/V/S/R install,
verify, select, and remove. Settings includes Automatic/Vulkan/CPU, CPU threads,
language, microphone, live insertion, clipboard, and existing policies.
Recording and downloads disable conflicting actions and explain why inline.

Unresolved release decisions: real-terminal Arabic screenshot/human approval,
screen-reader behavior across supported emulators, mouse affordances after the
keyboard baseline, and behavior when platform capture becomes available.
