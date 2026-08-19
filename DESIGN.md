---
name: OpenWhisper Precision Instrument
description: A restrained graphite-and-chalk control surface for private Linux dictation.
colors:
  recording-red: "#bd463b"
  recording-red-hover: "#aa392f"
  recording-coral-signal: "#c94f43"
  recording-ink: "#fff8f4"
  warm-canvas: "#d8d7d1"
  instrument-graphite: "#202221"
  instrument-graphite-hover: "#2b2d2c"
  warm-chalk: "#f1efe9"
  warm-chalk-secondary: "#e8e6df"
  control-face: "#dcdad3"
  measured-ink: "#1b1d1c"
  muted-ink: "#60625f"
  chalk-on-dark: "#f4f1e9"
  muted-chalk-on-dark: "#b9bab5"
  construction-seam: "#b9b8b1"
  construction-seam-dark: "#484a48"
  construction-seam-dark-strong: "#626461"
  status-green: "#1f6b3a"
  warning-amber: "#99651d"
  danger-red: "#a33e35"
  focus-blue: "#156e8b"
  dark-canvas: "#151716"
  dark-rail: "#111312"
  dark-rail-hover: "#292b2a"
  dark-face: "#1c1e1d"
  dark-face-secondary: "#222423"
  dark-control-face: "#2b2d2b"
  dark-measured-ink: "#f0eee7"
  dark-muted-ink: "#aaa9a3"
  dark-construction-seam: "#3b3d3b"
  dark-construction-seam-rail: "#464845"
  dark-construction-seam-strong: "#696b68"
  dark-recording-red: "#ef6a5c"
  dark-recording-red-hover: "#f47a6d"
  dark-recording-ink: "#171817"
  dark-status-green: "#78d295"
  dark-warning-amber: "#e2b164"
  dark-danger-red: "#f08b7d"
  dark-focus-blue: "#7bc6de"
typography:
  display:
    fontFamily: "Readex Pro Variable, sans-serif"
    fontSize: "clamp(1.8rem, 3.35vw, 3.65rem)"
    fontWeight: 390
    lineHeight: 1.42
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Readex Pro Variable, sans-serif"
    fontSize: "clamp(1.75rem, 3vw, 2.7rem)"
    fontWeight: 430
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Readex Pro Variable, sans-serif"
    fontSize: "1.08rem"
    fontWeight: 590
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Readex Pro Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  control:
    fontFamily: "Readex Pro Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 570
    lineHeight: 1.2
  technical:
    fontFamily: "IBM Plex Mono, monospace"
    fontSize: "0.68rem"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "0.055em"
rounded:
  checkbox: "5px"
  control: "10px"
  panel: "12px"
  round: "999px"
spacing:
  hairline: "1px"
  tight: "8px"
  control: "12px"
  field: "20px"
  roomy: "28px"
components:
  record-button:
    backgroundColor: "{colors.recording-red}"
    textColor: "{colors.recording-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.round}"
    size: "clamp(126px, 12vw, 162px)"
  command-button:
    backgroundColor: "transparent"
    textColor: "{colors.chalk-on-dark}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "44px"
  primary-action:
    backgroundColor: "{colors.recording-red}"
    textColor: "{colors.recording-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: "44px"
  instrument-panel:
    backgroundColor: "{colors.warm-chalk}"
    textColor: "{colors.measured-ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.field}"
---

# Design System: OpenWhisper Precision Instrument

## Overview

**Creative North Star: "The Precision Audio Instrument"**

OpenWhisper feels like a calibrated piece of studio equipment: quiet at rest,
unambiguous while recording, and dense only where technical confidence matters.
The graphite rail is the fixed chassis; chalk work surfaces hold the live task;
recording red is the single physical-looking control that demands attention.

The system is restrained rather than sterile. Warm neutrals, locally bundled
Arabic-capable Readex Pro, tabular technical readings, hairline seams, and
asymmetric working areas make the interface feel authored without decoration.
The React Capture bench is the main visible shell; the compact PySide overlay is
its deliberately native, non-focus-stealing companion while dictation runs in
another application. Gradients, glass effects, nested dashboard cards, and
ornamental motion remain outside this visual world.

**Key Characteristics:**

- One dominant action and one recording accent.
- A dark chassis around warm, high-legibility working surfaces.
- Large mixed-direction transcript content beside compact technical readings.
- Hairline construction seams, restrained corners, and almost no idle elevation.
- Responsive reflow that preserves the instrument hierarchy instead of shrinking it.
- Native tray and overlay affordances that keep Capture reachable across applications.

## Colors

The palette pairs warm graphite and chalk neutrals with a deliberately scarce
recording red. A brighter coral carries small live signals; green, amber, and
danger red always appear with iconography or text, never as the only state cue.
Dark mode substitutes the documented dark role tokens rather than tinting or
inverting the light palette.

### Primary

- **Recording Red:** The large record/stop control, onboarding primary action,
  selected checkbox, meter activity, and text selection.
- **Recording Coral Signal:** Small live indicators and signature icons where
  the primary control would be visually excessive.

### Neutral

- **Instrument Graphite:** Persistent top rail, technical reading rail, and
  high-contrast recovery actions.
- **Warm Chalk:** Transcript, control, drawer, and onboarding work surfaces.
- **Bench Canvas:** The narrow field around joined instrument plates.
- **Measured Ink:** Primary and secondary content tones.
- **Construction Seams:** Hairline divisions between controls and readings.

### Named Rules

**The One Signal Rule.** Recording color occupies only the control or state that
currently matters; never distribute it across unrelated labels and decoration.

**The Warm Neutral Rule.** Use the established graphite/chalk roles instead of
pure black and pure white for large surfaces in either theme.

**The Redundancy Rule.** Success, warning, failure, clipping risk, and recording
must remain understandable from icon, label, or meter structure without color.

## Typography

**Display Font:** Readex Pro Variable (with a system sans-serif fallback)  
**Body Font:** Readex Pro Variable (with a system sans-serif fallback)  
**Label/Mono Font:** IBM Plex Mono

**Character:** Readex Pro keeps Arabic, Latin, and code-switched content within
one coherent voice. IBM Plex Mono makes shortcuts, identifiers, elapsed time,
level measurements, and state readings feel measured rather than decorative.
Both families ship locally; no runtime font fetch is permitted.

### Hierarchy

- **Display** (390, fluid, 1.42): Live transcript and empty capture prompt only.
- **Headline** (430, fluid, 1.08): Onboarding step titles and exceptional
  full-surface boot/fatal headings.
- **Title** (590, compact): Current surface, modal, and plate headings.
- **Body** (400, 1rem, 1.5): Instructions, statuses, and dialog copy; explanatory
  prose stays near 54–62 characters per line.
- **Control** (570, 1rem): The dominant record/stop label.
- **Technical** (400, 0.68rem, tracked): Shortcuts, identifiers, elapsed time,
  dBFS output, and terse uppercase instrument labels.

### Named Rules

**The Measured Mono Rule.** IBM Plex Mono communicates machine state; it never
replaces Readex Pro for explanatory prose or user-authored transcript content.

## Layout

The desktop Capture bench fills the viewport below a 68px top rail. Its joined
control and transcript plates use an asymmetric 38/62 split, followed by a
five-cell technical rail. Separation comes from shared one-pixel seams and
controlled padding rather than floating cards. The screenshot at
`docs/images/openwhisper-capture.png` is the built recording-state reference.

At 900px and below, the control plate becomes a compact two-column assembly
above the transcript and the reading rail becomes two columns. At 560px and
below, the instrument, footer, and readings become one column and the top rail
removes secondary labels without hiding state or the 44px command/theme targets.
The command drawer becomes a bottom sheet at that width. At 700px and below,
onboarding becomes a full-viewport single-column flow with icon-only progress
markers and visually hidden but accessible step labels.

Logical properties are the layout vocabulary. The document remains English
LTR, while transcript text, provider names, identifiers, and readings that can
contain user or engine content determine direction independently. Long values
must wrap safely or truncate with a title that exposes the full value.

## Elevation & Depth

The system is flat by default. Tonal layers and one-pixel seams establish depth;
the command drawer, onboarding modal, and tactile record control are the only
raised elements. The record button lifts by two pixels on hover, compresses by
one pixel on press, and loses its shadow when disabled.

### Shadow Vocabulary

- **Raised Utility:** A restrained ambient shadow (`0 8px 24px rgb(24 25 24 / 14%)`)
  for the command drawer and onboarding modal; dark mode deepens it to 28% black.
- **Record Tactility:** A compact warm shadow (`0 9px 26px rgb(58 25 21 / 20%)`)
  tied only to the resting capture control, with one hover and one pressed variant.

### Named Rules

**The Chassis Stays Flat Rule.** Panels, rails, readings, notices, and resting
utility controls use color and seams—not shadows—to express hierarchy.

## Shapes

Panels use restrained 12px corners, while joined plates expose rounding only at
the outer edges. Utility controls use 10px corners and every actionable compact
control maintains a 44px target. The five-step onboarding marker and selected
checkbox use small circles and a 5px corner respectively. The record control is
the sole large recurring circle; its silhouette communicates capture. Hairline
borders stay precise, and the level meter deliberately uses square segments.

## Components

### Buttons

- **Record / Stop:** The circular red control centers a microphone or stop icon
  and a single verb. It starts only when the selected provider is available;
  processing or a pending engine request disables it. Recording keeps the red
  fill and gains a high-contrast inset ring. Stop and cancel remain separate.
- **Primary Action:** Onboarding Continue/Open Capture uses the same recording
  fill in a 44px, 10px-corner control; disabled state reduces opacity and blocks
  pointer activation.
- **Utility / Recovery:** Commands, appearance, test, back, cancel, restart,
  dismiss, and copy recovery are neutral until hover. Insertion failure with a
  preserved final transcript exposes **Copy preserved text** beside the result.
- **Hover / Press / Focus:** Pointer changes are subtle and state-specific.
  Every keyboard target uses the shared 2px focus outline with a 3px offset.

### Cards / Containers

- **Instrument Plates:** Joined chalk work surfaces with outside-only panel
  corners, hairline seams, compact headings, and a generous transcript field.
- **Technical Readings:** Five joined graphite definition-list cells expose the
  configured shortcut, provider/model, microphone, privacy boundary, and
  insertion outcome. Values are single-line and overflow-safe; state icons
  accompany inserted, copied, and failed results.
- **Notices:** A full-width tonal strip sits between the top rail and bench.
  Informational notices use `role="status"`, errors use `role="alert"`, and all
  notices have a 44px dismiss target.

### Navigation

The top rail is persistent, dark, and single-level. Brand, Capture title, live
state, command access, and theme cycle are instrument functions, not page
navigation. There is no sidebar, breadcrumb, Library, Personalize, or System
surface in the hybrid v0.1 spike.

### Audio Meter

The 18-segment dBFS meter is a semantic `meter` with numeric value text and a
visible −60 to 0 scale. Active segments use the recording token; the top two
segments gain a danger outline for clipping risk so color is not the only cue.
When idle, the readout is −∞ dBFS and its accessible value is “No input.”

### Capture State Surfaces

Booting is a minimal full-surface connection state. Ready, recording,
processing, cleaning, inserting, completed, cancelled, and failed share the
instrument layout and update the polite live state readout. Recording adds
elapsed time; processing-family states disable capture and retain cancel.
Recoverable engine/action errors appear as sanitized notices. Fatal engine loss
switches to **Capture stopped safely**, explains the no-silent-restart guarantee,
and exposes **Restart OpenWhisper**.

### Command Drawer

Ctrl/Cmd+K opens a dismissible React Aria modal. Rows expose Start, Stop and
Cancel only when valid for the engine state; the configured global shortcut is
shown only for Start, while the other rows are labeled as actions. Keyboard
focus is trapped and restored by the dialog primitive.

### Readiness Onboarding

First-run onboarding is a non-dismissible five-step React Aria dialog: privacy
boundary acknowledgment, microphone test, shortcut/portal diagnostics, selected
local-provider readiness, and completion. Continue is gated on an explicit
privacy check and completed microphone/shortcut checks, including attention
results; **Open Capture** additionally requires an available provider. Check,
saving, attention, and save-failure states remain inline and announced. On
narrow screens the progress rail moves above content and the dialog fills the
viewport.

### Native Tray and Overlay Companions

The Tauri tray offers Show OpenWhisper, Start/Stop dictation, and Quit. Closing
the main window hides it only when a StatusNotifier tray is available; otherwise
the window starts and remains reachable. The PySide overlay stays above other
applications without accepting focus, follows recording/processing state,
shows level and a clipped partial preview, provides Stop and Cancel, opens near
the bottom center, and remembers a user-dragged position.

## Do's and Don'ts

### Do:

- **Do** reserve recording red for the dominant action and live state.
- **Do** derive labels, availability, shortcuts, microphone, provider, and
  insertion results from engine state rather than sample copy.
- **Do** use React Aria semantics, full keyboard operation, visible focus,
  semantic live regions, and 44px compact targets.
- **Do** use `dir="auto"`, `unicode-bidi: plaintext` for transcript content,
  bidi isolation for compact values, and logical CSS throughout.
- **Do** disable all decorative animation, meter transitions, and record
  transforms when either system reduced-motion or the saved preference applies.
- **Do** keep transcript state in frontend memory and raw audio inside the
  Python engine; frontend IPC carries versioned state, metadata, text, and
  scalar audio-level events only.
- **Do** preserve the tray and non-focus-stealing Qt overlay as hybrid v0.1
  companions to the React Capture surface.

### Don't:

- **Don't** add gradients, glassmorphism, decorative glow, or translucent cards.
- **Don't** add secondary screens, nested cards, or a generic dashboard sidebar
  to this spike.
- **Don't** use remote fonts, images, scripts, telemetry, frontend network calls,
  localStorage, IndexedDB, or a service worker.
- **Don't** animate at rest or bypass either saved or system reduced-motion.
- **Don't** force Arabic, Latin, and mixed content into one fixed text direction.
- **Don't** expose transcript content in desktop notifications or raw audio over
  the Tauri/Python NDJSON boundary.
- **Don't** remove the Qt parity path or broaden the cutover until the built
  Flatpak passes real capture, insertion, overlay, tray-reachability, and portal
  tests on both GNOME and KDE Wayland; failure on either desktop stops cutover.
