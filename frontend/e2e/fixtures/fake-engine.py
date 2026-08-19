#!/usr/bin/env python3
"""Deterministic protocol peer used only by the feature-gated WebKit E2E build."""

from __future__ import annotations

import json
import sys


SETTINGS = {
    "transcriptionProvider": "faster-whisper",
    "transcriptionModel": "large-v3-turbo",
    "device": "cpu",
    "language": "auto",
    "cleanupMode": "raw",
    "cleanupProvider": "none",
    "customCleanupPrompt": "",
    "shortcutMode": "toggle",
    "shortcut": "<alt>+o",
    "liveInsertion": False,
    "retentionDays": 30,
    "notifications": False,
    "activeModeId": "raw",
    "onboardingCompleted": True,
    "theme": "system",
    "reducedMotion": False,
    "retainAudio": False,
    "audioRetentionDays": 7,
    "audioDeviceId": None,
}
PROVIDER = {
    "id": "faster-whisper",
    "name": "Faster Whisper",
    "description": "Local, private transcription.",
    "models": ["large-v3-turbo"],
    "supportsStreaming": True,
    "needsApiKey": False,
    "available": True,
    "unavailableReason": None,
    "supportsTranscription": True,
    "supportsCleanup": False,
}
METHODS = [
    "app.bootstrap",
    "dictation.start",
    "dictation.stop",
    "dictation.cancel",
    "settings.update",
]
sequence = 0


def write(frame: dict) -> None:
    sys.stdout.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request: dict, result: object) -> None:
    write({"v": 1, "kind": "response", "id": request["id"], "ok": True, "result": result})


def event(name: str, payload: dict) -> None:
    global sequence
    sequence += 1
    write({"v": 1, "kind": "event", "seq": sequence, "event": name, "payload": payload})


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "app.bootstrap":
        response(
            request,
            {
                "protocolVersion": 1,
                "engineSessionId": "e2e-engine",
                "firstRun": False,
                "settings": SETTINGS,
                "providers": [PROVIDER],
                "dictation": {"state": "idle", "sessionId": None},
                "availableMethods": METHODS,
            },
        )
    elif method == "dictation.start":
        response(request, {"accepted": True})
        event("dictation.state", {"state": "recording", "sessionId": "e2e-session"})
        event(
            "dictation.partial",
            {"text": "مرحبا OpenWhisper — capture is ready.", "sessionId": "e2e-session"},
        )
        event(
            "dictation.audioLevel",
            {"rms": 0.58, "peak": 0.74, "elapsed": 3.0, "sessionId": "e2e-session"},
        )
    elif method == "dictation.stop":
        response(request, {"accepted": True})
        event("dictation.state", {"state": "processing", "sessionId": "e2e-session"})
        event(
            "dictation.completed",
            {
                "text": "مرحبا OpenWhisper — capture is ready.",
                "provider": "faster-whisper",
                "inserted": True,
                "insertionMethod": "atspi",
                "sessionId": "e2e-session",
            },
        )
        event("dictation.state", {"state": "completed", "sessionId": "e2e-session"})
    elif method == "dictation.cancel":
        response(request, {"accepted": True})
        event("dictation.state", {"state": "cancelled", "sessionId": "e2e-session"})
    elif method == "settings.update":
        SETTINGS.update(request.get("params", {}).get("changes", {}))
        response(request, SETTINGS)
    else:
        write(
            {
                "v": 1,
                "kind": "response",
                "id": request.get("id", "protocol"),
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": "Method unavailable."},
            }
        )
