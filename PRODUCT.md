# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React, strict TypeScript, Vite, and React Aria Components in a Tauri/WebKitGTK desktop shell. Tauri owns native window, tray, notification, lifecycle, and supervised-process behavior. The existing Python runtime remains the engine for audio, transcription, insertion, credentials, storage, provider integrations, Linux shortcuts, and the temporary PySide recording overlay.

## Users

OpenWhisper serves Linux desktop users who dictate into other applications, especially people working in Arabic, English, or mixed-direction text. They need a fast, trustworthy capture control that remains available across applications without turning their speech or context into a new data surface.

## Product Purpose

OpenWhisper provides privacy-first system-wide dictation on Linux. A successful session starts from the window, tray, or a global shortcut; makes recording and processing state unmistakable; transcribes through a selected local or explicitly configured cloud provider; and inserts the result into the intended application with a safe clipboard fallback.

## Positioning

OpenWhisper combines first-class Arabic and mixed-direction dictation, Linux-native insertion and portal shortcuts, and a local-process privacy boundary: raw audio remains inside the Python engine and never crosses frontend IPC.

## Operating Context

The application runs primarily in the background on GNOME and KDE Wayland, with X11 support. Users invoke dictation while another application has focus, watch a non-focus-stealing overlay, then return to the main window for provider state and configuration. Flatpak is the v0.1 distribution and update path. The first hybrid milestone contains Capture, tray lifecycle, shortcuts, microphone capture, insertion, and the existing overlay; secondary surfaces wait for GNOME and KDE Wayland validation.

## Capabilities and Constraints

- Preserve the OpenWhisper CLI, Flatpak ID `io.github.yousufaltayeb.OpenWhisper`, configuration, databases, credentials, storage locations, privacy defaults, signed remotes, and update compatibility.
- The visible shell uses no CDN, remote script, service worker, runtime frontend network request, localStorage, or IndexedDB. Fonts, icons, and assets ship locally.
- Tauri communicates with `openwhisper-engine` only through private versioned NDJSON over child stdin/stdout. Raw audio never enters IPC.
- Python continues to own recording, transcription, providers, retention, insertion, credentials, Qt Multimedia, QtDBus, Linux shortcuts, and the PySide overlay.
- English is the only v0.1 interface locale. Arabic and mixed-direction user content are release-blocking and use `dir="auto"`, bidi isolation, logical CSS, Arabic-capable fonts, and locale-aware formatting.
- Theme preference is `system`, `light`, or `dark`; motion follows both system reduced-motion and the saved OpenWhisper preference.

## Brand Commitments

Keep the OpenWhisper name and technical, privacy-forward identity. The replacement visual direction is a restrained precision audio instrument: graphite and chalk surfaces, one recording accent, exact spacing, limited elevation and radii, and functional audio visualization. Readex Pro is the Arabic/Latin interface face; IBM Plex Mono is reserved for shortcuts, model identifiers, latency, and technical readings. No gradients, glassmorphism, decorative motion, card nesting, or generic dashboard voice.

## Evidence on Hand

The repository contains the working Python runtime and 121-test baseline, the current Qt interface and overlay, production Flatpak metadata, Arabic/English documentation, and existing product screenshots under `docs/images/`. There are no customer claims, benchmarks, testimonials, or telemetry claims to invent.

## Product Principles

- Keep speech private by construction, not by reassurance copy.
- Make capture state and recovery legible at a glance.
- Treat Arabic, English, and mixed-direction content as equally real input.
- Preserve working Linux-native behavior while replacing the visible shell incrementally.
- Stop the cutover if the hybrid Flatpak cannot pass real GNOME and KDE capture and portal tests.

## Accessibility & Inclusion

Use accessible React Aria primitives, full keyboard navigation, visible focus, focus restoration, reduced motion, high-contrast light and dark themes, semantic status announcements, and release-blocking Arabic/mixed-direction content coverage. The document root remains English LTR; user-authored content determines its own direction.
