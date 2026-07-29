from __future__ import annotations

import json
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from openwhisper.providers.transport import HttpRequest, HttpResponse


class FakeTransport:
    """Deterministic HTTP transport: never performs a network request."""

    def __init__(self, *responses: HttpResponse | BaseException) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status: int, payload: object) -> HttpResponse:
    return HttpResponse(status, {}, json.dumps(payload).encode("utf-8"))


@pytest.fixture
def audio_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 16_000)
    yield path
