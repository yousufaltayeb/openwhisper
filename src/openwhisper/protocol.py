"""Private versioned NDJSON protocol used by the Tauri host.

The protocol never opens a socket.  Frames travel only over the supervised
engine child's stdin/stdout, and stdout is reserved exclusively for frames.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from enum import StrEnum
from typing import Any, BinaryIO

from .contracts import AppSettings, from_wire_keys, to_wire
from .core.audio import AudioDeviceError
from .core.session import SessionBusyError
from .providers.errors import ProviderError

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    BUSY = "BUSY"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    INTERNAL = "INTERNAL"


class ProtocolError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def encode_frame(frame: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            frame,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.INTERNAL, "A protocol frame could not be encoded.") from exc
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The protocol frame is too large.")
    return encoded + b"\n"


def decode_frame(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "An empty protocol frame was received.")
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The protocol frame is too large.")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(
            ErrorCode.INVALID_ARGUMENT, "A malformed protocol frame was received."
        ) from exc
    if not isinstance(decoded, dict):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "Protocol frames must be JSON objects.")
    return decoded


def read_frames(stream: BinaryIO, *, chunk_size: int = 64 * 1024) -> Iterator[dict[str, Any]]:
    """Read fragmented or coalesced NDJSON frames with a bounded buffer."""

    buffer = bytearray()
    # BufferedReader.read(size) is allowed to wait until ``size`` bytes arrive.
    # That is correct for ordinary files but deadlocks a long-running NDJSON
    # pipe after a small request. ``read1`` performs at most one raw read and
    # returns the bytes currently available, while BytesIO and test doubles
    # continue to use the regular BinaryIO method.
    read_chunk = getattr(stream, "read1", stream.read)
    while True:
        chunk = read_chunk(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                if len(buffer) > MAX_FRAME_BYTES:
                    raise ProtocolError(
                        ErrorCode.INVALID_ARGUMENT, "The protocol frame is too large."
                    )
                break
            raw = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            yield decode_frame(raw)
    if buffer:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The final protocol frame was incomplete.")


class ProtocolWriter:
    """Serialize all stdout writes so concurrent runtime events cannot interleave."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.RLock()

    def write(self, frame: Mapping[str, Any]) -> None:
        encoded = encode_frame(frame)
        with self._lock:
            self._stream.write(encoded)
            self._stream.flush()


class EventNormalizer:
    """Translate the legacy runtime stream into stable public engine events."""

    _DICTATION_EVENTS = {
        "state": "dictation.state",
        "partial": "dictation.partial",
        "audio-level": "dictation.audioLevel",
        "transcript": "dictation.completed",
    }

    def __init__(self, emit: Callable[[Mapping[str, Any]], None]) -> None:
        self._emit = emit
        self._sequence = 0
        self._session_id: str | None = None
        self._state = "idle"
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    def runtime_event(self, event: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if event == "state":
                state = str(payload.get("state", "idle"))
                if state == "recording" and self._state != "recording":
                    self._session_id = str(uuid.uuid4())
                self._state = state
            public_event = self._DICTATION_EVENTS.get(event)
            if event == "history-changed":
                public_event = "history.changed"
            elif event == "provider-progress":
                public_event = "provider.progress"
            elif event == "shortcut-status":
                public_event = "shortcut.status"
            elif event in {"models.progress", "model-progress"}:
                public_event = "models.progress"
                payload = _sanitize_model_payload(payload)
            elif event in {"models.changed", "model-changed"}:
                public_event = "models.changed"
                payload = _sanitize_model_payload(payload)
            elif event in {"compute.changed", "compute-capabilities"}:
                public_event = "compute.changed"
                payload = _sanitize_compute_payload(payload)
            elif event in {"info", "warning", "error"}:
                public_event = "notice"
                payload = {"level": event, "message": str(payload.get("message", ""))}
            if public_event is None:
                return
            self._sequence += 1
            normalized = dict(payload)
            if public_event.startswith("dictation."):
                normalized["session_id"] = self._session_id
            frame = {
                "v": PROTOCOL_VERSION,
                "kind": "event",
                "seq": self._sequence,
                "event": public_event,
                "payload": to_wire(normalized),
            }
        self._emit(frame)


class EngineApi:
    """Allowlisted mapping from protocol methods to the existing runtime."""

    methods = frozenset(
        {
            "app.bootstrap",
            "app.shutdown",
            "dictation.start",
            "dictation.stop",
            "dictation.cancel",
            "settings.get",
            "settings.update",
            "modes.select",
            "audio.listDevices",
            "audio.testDevice",
            "diagnostics.run",
            "providers.list",
            "models.list",
            "models.download",
            "models.cancel",
            "models.remove",
            "compute.capabilities",
            "compute.probe",
            "app.restartEngine",
        }
    )

    def __init__(
        self,
        controller: Any,
        events: EventNormalizer,
        *,
        on_shutdown: Callable[[], None] | None = None,
        engine_session_id: str | None = None,
    ) -> None:
        self.controller = controller
        self.events = events
        self.on_shutdown = on_shutdown or controller.shutdown
        self.engine_session_id = engine_session_id or str(uuid.uuid4())

    def invoke(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in self.methods:
            raise ProtocolError(
                ErrorCode.NOT_FOUND, "The requested engine method is not available."
            )
        handler = getattr(self, f"_handle_{method.replace('.', '_')}")
        return handler(params)

    def _handle_app_bootstrap(self, params: Mapping[str, Any]) -> dict[str, Any]:
        _require_empty(params)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "engineSessionId": self.engine_session_id,
            "firstRun": bool(self.controller.is_first_run()),
            "settings": to_wire(self.controller.settings()),
            "providers": to_wire(self.controller.providers()),
            "dictation": {
                "state": self.events.state,
                "sessionId": self.events.session_id,
            },
            "availableMethods": sorted(self.methods),
        }

    def _handle_app_shutdown(self, params: Mapping[str, Any]) -> dict[str, bool]:
        _require_empty(params)
        self.on_shutdown()
        return {"accepted": True}

    def _handle_dictation_start(self, params: Mapping[str, Any]) -> dict[str, bool]:
        _require_empty(params)
        self.controller.start_recording()
        return {"accepted": True}

    def _handle_dictation_stop(self, params: Mapping[str, Any]) -> dict[str, bool]:
        _require_empty(params)
        self.controller.stop_recording()
        return {"accepted": True}

    def _handle_dictation_cancel(self, params: Mapping[str, Any]) -> dict[str, bool]:
        _require_empty(params)
        self.controller.cancel()
        return {"accepted": True}

    def _handle_settings_get(self, params: Mapping[str, Any]) -> Any:
        _require_empty(params)
        return to_wire(self.controller.settings())

    def _handle_settings_update(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, {"changes"})
        changes = params.get("changes")
        if not isinstance(changes, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "Settings changes must be an object.")
        translated = from_wire_keys(changes)
        known = set(AppSettings.__dataclass_fields__)
        if not translated.keys() <= known:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "One or more settings are unknown.")
        _validate_setting_changes(translated)
        current = self.controller.settings()
        updated = replace(current, **translated)
        self.controller.save_settings(updated)
        return to_wire(self.controller.settings())

    def _handle_modes_select(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, {"modeId"})
        mode_id = params.get("modeId")
        if not isinstance(mode_id, str) or not mode_id.strip():
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A mode identifier is required.")
        self.controller.select_mode(mode_id)
        return {"activeModeId": self.controller.settings().active_mode_id}

    def _handle_audio_listDevices(self, params: Mapping[str, Any]) -> Any:  # noqa: N802
        _require_empty(params)
        return to_wire(self.controller.audio_devices())

    def _handle_audio_testDevice(self, params: Mapping[str, Any]) -> Any:  # noqa: N802
        _require_keys(params, set(), optional={"deviceId"})
        device_id = params.get("deviceId")
        if device_id is not None and not isinstance(device_id, str):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The microphone identifier is invalid.")
        ready, message = self.controller.test_microphone(device_id)
        return {"ready": bool(ready), "message": str(message)}

    def _handle_diagnostics_run(self, params: Mapping[str, Any]) -> Any:
        _require_empty(params)
        return to_wire(self.controller.readiness_checks())

    def _handle_providers_list(self, params: Mapping[str, Any]) -> Any:
        _require_empty(params)
        return to_wire(self.controller.providers())

    def _handle_models_list(self, params: Mapping[str, Any]) -> Any:
        _require_empty(params)
        return to_wire(self.controller.models_list())

    def _handle_models_download(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, {"modelId"})
        model_id = params.get("modelId")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A model identifier is required.")
        return to_wire(self.controller.models_download(model_id))

    def _handle_models_cancel(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, set(), optional={"jobId", "modelId"})
        identifier = params.get("jobId") or params.get("modelId")
        if not isinstance(identifier, str) or not identifier.strip() or (
            "jobId" in params and "modelId" in params
        ):
            raise ProtocolError(
                ErrorCode.INVALID_ARGUMENT,
                "A single model or job identifier is required.",
            )
        return to_wire(self.controller.models_cancel(identifier))

    def _handle_models_remove(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, {"modelId"})
        model_id = params.get("modelId")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A model identifier is required.")
        return to_wire(self.controller.models_remove(model_id))

    def _handle_compute_capabilities(self, params: Mapping[str, Any]) -> Any:
        _require_empty(params)
        return to_wire(self.controller.compute_capabilities())

    def _handle_compute_probe(self, params: Mapping[str, Any]) -> Any:
        _require_keys(params, set(), optional={"backend", "compute"})
        backend = params.get("backend", params.get("compute"))
        if backend is not None and not isinstance(backend, str):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The compute backend is invalid.")
        return to_wire(self.controller.probe_compute(backend))

    def _handle_app_restartEngine(self, params: Mapping[str, Any]) -> Any:  # noqa: N802
        _require_empty(params)
        restart = getattr(self.controller, "restart_engine", None)
        if not callable(restart):
            raise ProtocolError(ErrorCode.UNAVAILABLE, "Engine restart is unavailable.")
        return to_wire(restart())


class ProtocolDispatcher:
    """Validate request envelopes, handshake once, and sanitize all failures."""

    def __init__(self, api: EngineApi) -> None:
        self.api = api
        self._seen_ids: set[str] = set()
        self._handshake_complete = False
        self._lock = threading.RLock()

    def dispatch(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        request_id = frame.get("id") if isinstance(frame.get("id"), str) else "protocol"
        try:
            with self._lock:
                version, request_id, method, params = self._validate(frame)
                del version
                if request_id in self._seen_ids:
                    raise ProtocolError(
                        ErrorCode.INVALID_ARGUMENT,
                        "Each protocol request identifier must be unique.",
                    )
                self._seen_ids.add(request_id)
                if not self._handshake_complete and method != "app.bootstrap":
                    raise ProtocolError(
                        ErrorCode.PROTOCOL_MISMATCH,
                        "Complete the protocol handshake before calling engine methods.",
                    )
                result = self.api.invoke(method, params)
                if method == "app.bootstrap":
                    self._handshake_complete = True
            return _response(request_id, ok=True, result=to_wire(result))
        except Exception as exc:
            error = _public_error(exc)
            return _response(
                request_id,
                ok=False,
                error={"code": error.code.value, "message": error.message},
            )

    @staticmethod
    def _validate(frame: Mapping[str, Any]) -> tuple[int, str, str, Mapping[str, Any]]:
        if frame.get("v") != PROTOCOL_VERSION:
            raise ProtocolError(
                ErrorCode.PROTOCOL_MISMATCH,
                f"OpenWhisper engine protocol version {PROTOCOL_VERSION} is required.",
            )
        if frame.get("kind") != "request":
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A request frame was expected.")
        request_id = frame.get("id")
        if not isinstance(request_id, str):
            raise ProtocolError(
                ErrorCode.INVALID_ARGUMENT, "A UUID request identifier is required."
            )
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError) as exc:
            raise ProtocolError(
                ErrorCode.INVALID_ARGUMENT, "A UUID request identifier is required."
            ) from exc
        method = frame.get("method")
        if not isinstance(method, str) or not method:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "An engine method is required.")
        params = frame.get("params", {})
        if not isinstance(params, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "Request parameters must be an object.")
        return PROTOCOL_VERSION, request_id, method, params


def _response(
    request_id: str,
    *,
    ok: bool,
    result: Any = None,
    error: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "kind": "response",
        "id": request_id,
        "ok": ok,
    }
    if ok:
        frame["result"] = result
    else:
        frame["error"] = dict(error or {})
    return frame


def _public_error(exc: Exception) -> ProtocolError:
    if isinstance(exc, ProtocolError):
        return exc
    if isinstance(exc, SessionBusyError):
        return ProtocolError(ErrorCode.BUSY, "OpenWhisper is busy with another dictation.")
    if isinstance(exc, AudioDeviceError):
        return ProtocolError(ErrorCode.UNAVAILABLE, "The selected microphone is unavailable.")
    if isinstance(exc, ProviderError):
        return ProtocolError(ErrorCode.PROVIDER_ERROR, "The provider operation failed.")
    if isinstance(exc, (ValueError, TypeError)):
        return ProtocolError(ErrorCode.INVALID_ARGUMENT, "The request arguments were invalid.")
    if isinstance(exc, KeyError):
        return ProtocolError(ErrorCode.NOT_FOUND, "The requested item was not found.")
    if isinstance(exc, PermissionError):
        return ProtocolError(ErrorCode.PERMISSION_DENIED, "The operation was not permitted.")
    if isinstance(exc, (FileNotFoundError, ConnectionError, TimeoutError)):
        return ProtocolError(ErrorCode.UNAVAILABLE, "The requested service is unavailable.")
    return ProtocolError(ErrorCode.INTERNAL, "OpenWhisper could not complete the request.")


def _require_empty(params: Mapping[str, Any]) -> None:
    if params:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "This method takes no parameters.")


def _require_keys(
    params: Mapping[str, Any], required: set[str], *, optional: set[str] | None = None
) -> None:
    optional = optional or set()
    if not required <= params.keys() or not params.keys() <= required | optional:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "The request parameters are invalid.")


def _validate_setting_changes(changes: Mapping[str, Any]) -> None:
    boolean_fields = {
        "live_insertion",
        "notifications",
        "onboarding_completed",
        "reduced_motion",
        "retain_audio",
    }
    integer_fields = {"retention_days", "audio_retention_days"}
    optional_string_fields = {"audio_device_id"}
    choice_fields = {
        "device": {"auto", "cpu", "nvidia", "amd", "cuda"},
        "output_mode": {"insert", "clipboard", "both"},
    }
    for name, value in changes.items():
        if name in boolean_fields and not isinstance(value, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A boolean setting was invalid.")
        if name in integer_fields and (not isinstance(value, int) or isinstance(value, bool)):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A numeric setting was invalid.")
        if name in optional_string_fields:
            if value is not None and not isinstance(value, str):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A device setting was invalid.")
        elif name in choice_fields and (
            not isinstance(value, str) or value not in choice_fields[name]
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A setting choice was invalid.")
        elif (
            name not in boolean_fields | integer_fields
            and name not in choice_fields
            and not isinstance(value, str)
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENT, "A text setting was invalid.")


def _sanitize_model_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep model events to public status fields; never expose local paths."""

    allowed = {
        "modelId",
        "jobId",
        "state",
        "progress",
        "installedSize",
        "error",
    }
    result = {key: payload[key] for key in allowed if key in payload}
    if "progress" in result:
        try:
            result["progress"] = max(0.0, min(1.0, float(result["progress"])))
        except (TypeError, ValueError):
            result["progress"] = None
    if "installedSize" in result:
        try:
            result["installedSize"] = max(0, int(result["installedSize"]))
        except (TypeError, ValueError):
            result["installedSize"] = 0
    if result.get("error") not in {None, "Model download failed.", "Model download was cancelled."}:
        result["error"] = "Model download failed."
    return result


def _sanitize_compute_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"id", "backend", "available", "validated", "supportedComputeTypes", "failureReason"}
    return {key: payload[key] for key in allowed if key in payload}
