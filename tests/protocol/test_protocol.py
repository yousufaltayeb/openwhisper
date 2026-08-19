from __future__ import annotations

import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from openwhisper.contracts import AppSettings, ProviderOption, to_wire
from openwhisper.core.session import SessionBusyError
from openwhisper.protocol import (
    MAX_FRAME_BYTES,
    EngineApi,
    ErrorCode,
    EventNormalizer,
    ProtocolDispatcher,
    ProtocolError,
    ProtocolWriter,
    decode_frame,
    encode_frame,
    read_frames,
)


def request(method: str, params: dict | None = None, *, version: int = 1) -> dict:
    return {
        "v": version,
        "kind": "request",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params or {},
    }


class FragmentedBytes:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = iter(chunks)

    def read(self, _size: int = -1) -> bytes:
        return next(self.chunks, b"")


class PersistentBufferedPipe:
    """Model a buffered pipe whose regular read waits for a full buffer."""

    def __init__(self, payload: bytes) -> None:
        self.chunks = iter((payload, b""))

    def read(self, _size: int = -1) -> bytes:
        raise AssertionError("a persistent buffered pipe must be consumed with read1")

    def read1(self, _size: int = -1) -> bytes:
        return next(self.chunks)


def test_frame_reader_handles_fragmented_coalesced_unicode_and_newlines() -> None:
    first = request("app.bootstrap")
    second = request("settings.update", {"changes": {"customCleanupPrompt": "سطر\nline"}})
    payload = encode_frame(first) + encode_frame(second)
    stream = FragmentedBytes([payload[:5], payload[5:37], payload[37:]])

    assert list(read_frames(stream)) == [first, second]
    assert decode_frame(encode_frame(second).rstrip(b"\n")) == second


def test_frame_reader_does_not_wait_for_a_full_persistent_pipe_buffer() -> None:
    frame = request("app.bootstrap")

    assert list(read_frames(PersistentBufferedPipe(encode_frame(frame)))) == [frame]


def test_frame_reader_rejects_oversized_and_incomplete_frames() -> None:
    with pytest.raises(ProtocolError) as oversized:
        list(read_frames(io.BytesIO(b"x" * (MAX_FRAME_BYTES + 1))))
    assert oversized.value.code is ErrorCode.INVALID_ARGUMENT

    with pytest.raises(ProtocolError, match="incomplete"):
        list(read_frames(io.BytesIO(b'{"v":1}')))


def test_writer_serializes_unicode_frames() -> None:
    destination = io.BytesIO()
    writer = ProtocolWriter(destination)
    writer.write({"text": "مرحبا\nhello"})
    assert json.loads(destination.getvalue()) == {"text": "مرحبا\nhello"}


@dataclass(frozen=True)
class Timestamped:
    created_at: datetime
    status: ErrorCode


def test_contract_serializer_uses_camel_case_utc_and_string_enums() -> None:
    value = Timestamped(datetime(2026, 7, 30, 12, 30, tzinfo=UTC), ErrorCode.BUSY)
    assert to_wire(value) == {
        "createdAt": "2026-07-30T12:30:00Z",
        "status": "BUSY",
    }


class Controller:
    def __init__(self) -> None:
        self.current = AppSettings()
        self.starts = 0
        self.stops = 0
        self.cancelled = 0
        self.selected = None
        self.closed = False

    def is_first_run(self):
        return True

    def settings(self):
        return self.current

    def providers(self):
        return (ProviderOption("local", "Local", "Private"),)

    def save_settings(self, settings):
        self.current = settings

    def select_mode(self, mode_id):
        self.selected = mode_id
        self.current.active_mode_id = mode_id

    def start_recording(self):
        self.starts += 1

    def stop_recording(self):
        self.stops += 1

    def cancel(self):
        self.cancelled += 1

    def audio_devices(self):
        return ()

    def test_microphone(self, device_id=None):
        return True, f"ready:{device_id or 'default'}"

    def readiness_checks(self):
        return {"microphone": "ready"}

    def shutdown(self):
        self.closed = True


def dispatcher(controller: Controller | None = None):
    target = controller or Controller()
    frames = []
    events = EventNormalizer(frames.append)
    return target, frames, ProtocolDispatcher(EngineApi(target, events))


def handshake(protocol: ProtocolDispatcher) -> dict:
    result = protocol.dispatch(request("app.bootstrap"))
    assert result["ok"] is True
    return result


def test_dispatch_requires_handshake_version_and_unique_request_ids() -> None:
    _controller, _events, protocol = dispatcher()
    early = protocol.dispatch(request("settings.get"))
    assert early["error"]["code"] == "PROTOCOL_MISMATCH"

    mismatch = protocol.dispatch(request("app.bootstrap", version=2))
    assert mismatch["error"]["code"] == "PROTOCOL_MISMATCH"

    frame = request("app.bootstrap")
    assert protocol.dispatch(frame)["ok"] is True
    duplicate = protocol.dispatch(frame)
    assert duplicate["error"]["code"] == "INVALID_ARGUMENT"


def test_dispatch_allowlist_settings_and_dedicated_mode_selection() -> None:
    controller, _events, protocol = dispatcher()
    bootstrap = handshake(protocol)
    assert bootstrap["result"]["firstRun"] is True
    assert bootstrap["result"]["settings"]["theme"] == "system"
    assert bootstrap["result"]["settings"]["onboardingCompleted"] is True

    changed = protocol.dispatch(
        request("settings.update", {"changes": {"theme": "dark", "reducedMotion": True}})
    )
    assert changed["result"]["theme"] == "dark"
    assert controller.current.reduced_motion is True

    completed = protocol.dispatch(
        request("settings.update", {"changes": {"onboardingCompleted": True}})
    )
    assert completed["result"]["onboardingCompleted"] is True

    invalid_completion = protocol.dispatch(
        request("settings.update", {"changes": {"onboardingCompleted": "yes"}})
    )
    assert invalid_completion["error"]["code"] == "INVALID_ARGUMENT"

    updated_mode = protocol.dispatch(
        request("settings.update", {"changes": {"activeModeId": "message"}})
    )
    assert updated_mode["result"]["activeModeId"] == "message"
    assert controller.current.active_mode_id == "message"
    selected = protocol.dispatch(request("modes.select", {"modeId": "message"}))
    assert selected["result"] == {"activeModeId": "message"}

    unknown = protocol.dispatch(request("history.search"))
    assert unknown["error"] == {
        "code": "NOT_FOUND",
        "message": "The requested engine method is not available.",
    }


def test_dispatch_sanitizes_errors_without_request_or_stack_content() -> None:
    controller, _events, protocol = dispatcher()
    handshake(protocol)

    def busy():
        raise SessionBusyError("private transcript and stack details")

    controller.start_recording = busy
    response = protocol.dispatch(request("dictation.start"))
    serialized = json.dumps(response)
    assert response["error"]["code"] == "BUSY"
    assert "private transcript" not in serialized
    assert "Traceback" not in serialized


def test_events_have_monotonic_sequences_and_dictation_session_ids() -> None:
    emitted: list[dict] = []
    events = EventNormalizer(emitted.append)
    events.runtime_event("state", {"state": "recording"})
    events.runtime_event("partial", {"text": "مرحبا\nhello"})
    events.runtime_event("audio-level", {"rms": 0.4})
    events.runtime_event("state", {"state": "processing"})
    events.runtime_event(
        "transcript",
        {"text": "done", "inserted": False, "insertion_method": "clipboard"},
    )

    assert [frame["seq"] for frame in emitted] == [1, 2, 3, 4, 5]
    assert {frame["payload"]["sessionId"] for frame in emitted} == {
        emitted[0]["payload"]["sessionId"]
    }
    assert emitted[1]["payload"]["text"] == "مرحبا\nhello"
    assert emitted[-1]["payload"]["insertionMethod"] == "clipboard"
    assert emitted[-1]["payload"]["inserted"] is False


def test_informational_runtime_events_remain_informational_notices() -> None:
    emitted: list[dict] = []
    events = EventNormalizer(emitted.append)

    events.runtime_event("info", {"message": "Transcript copied."})

    assert emitted == [
        {
            "v": 1,
            "kind": "event",
            "seq": 1,
            "event": "notice",
            "payload": {"level": "info", "message": "Transcript copied."},
        }
    ]


def test_shutdown_is_acknowledged_without_exposing_internal_state() -> None:
    controller, _events, protocol = dispatcher()
    handshake(protocol)
    response = protocol.dispatch(request("app.shutdown"))
    assert response["result"] == {"accepted": True}
    assert controller.closed
