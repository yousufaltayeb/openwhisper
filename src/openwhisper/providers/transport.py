"""Small injectable HTTP boundary shared by cloud adapters."""

from __future__ import annotations

import json
import mimetypes
import secrets
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .contracts import CancellationToken


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float = 30.0
    cancellation: CancellationToken | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class UrllibTransport:
    """Dependency-free production transport; tests inject a fake transport."""

    def send(self, request: HttpRequest) -> HttpResponse:
        if request.cancellation is not None and request.cancellation.is_set():
            raise TimeoutError("request cancelled before transport dispatch")
        raw_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with urllib.request.urlopen(raw_request, timeout=request.timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            # Return status only. The adapter intentionally discards provider
            # error bodies because they can echo transcript/request content.
            return HttpResponse(exc.code, dict(exc.headers.items()), b"")
        except TimeoutError as exc:
            raise TimeoutError from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError from exc
            raise ConnectionError("provider connection failed") from exc


def json_body(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json_object(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def encode_multipart(
    fields: dict[str, str],
    *,
    file_path: Path,
    file_data: bytes,
    file_field: str = "file",
) -> tuple[bytes, str]:
    boundary = f"openwhisper-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    safe_name = file_path.name.replace('"', "")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
