from __future__ import annotations

import json
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import FakeTransport, response

from openwhisper.providers import (
    CleanupContext,
    CleanupRequest,
    CohereCleanupProvider,
    ConnectionTestStatus,
    ContextSource,
    CoreTranscriptionAdapter,
    CredentialStore,
    FasterWhisperProvider,
    LlamaServer,
    LlamaServerConfig,
    OpenAITranscriptionProvider,
    ProviderError,
    ProviderErrorKind,
    Qwen3LocalCleanupProvider,
    TranscriptionRequest,
)
from openwhisper.providers.streaming import StablePrefixReconciler
from openwhisper.providers.transport import HttpResponse


class _Portal:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def retrieve_secret(self, token: str | None = None) -> tuple[bytes, str | None]:
        self.calls.append(token)
        return b"a per application portal master secret", "opaque-portal-token"


def test_portal_credentials_are_encrypted_and_environment_still_wins(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    portal = _Portal()
    store = CredentialStore(portal=portal, storage_path=path, environment={})
    store.set("openai", "portal-api-key")

    assert store.persistent
    assert store.get("openai") == "portal-api-key"
    assert "portal-api-key" not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reloaded = CredentialStore(
        portal=portal,
        storage_path=path,
        environment={"OPENAI_API_KEY": "temporary-environment-key"},
    )
    assert reloaded.get("openai") == "temporary-environment-key"
    restored = CredentialStore(portal=portal, storage_path=path, environment={})
    assert restored.get("openai") == "portal-api-key"


def test_credentials_are_session_only_without_the_secret_portal(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = CredentialStore(storage_path=path, environment={})
    store.set("groq", "session-key")

    assert not store.persistent
    assert store.get("groq") == "session-key"
    assert not path.exists()
    assert CredentialStore(storage_path=path, environment={}).get("groq") is None


def test_cloud_connection_probe_is_real_but_contains_no_dictation() -> None:
    transport = FakeTransport(response(200, {"message": {"content": [{"text": "ok"}]}}))
    provider = CohereCleanupProvider(api_key="test-secret", transport=transport)

    result = provider.test_connection()

    assert result.status is ConnectionTestStatus.SUCCESS
    body = json.loads(transport.requests[0].body)
    assert "<transcript>\nok\n</transcript>" in body["messages"][1]["content"]
    assert "test-secret" not in body["messages"][1]["content"]


@pytest.mark.parametrize(
    ("response_status", "kind"),
    [
        (401, ProviderErrorKind.AUTHENTICATION),
        (429, ProviderErrorKind.RATE_LIMIT),
        (404, ProviderErrorKind.MODEL),
    ],
)
def test_connection_probes_classify_provider_failures(
    response_status: int, kind: ProviderErrorKind
) -> None:
    provider = CohereCleanupProvider(
        api_key="test-secret",
        transport=FakeTransport(HttpResponse(response_status, {}, b"credential=test-secret")),
    )

    result = provider.test_connection()

    assert result.status is ConnectionTestStatus.FAILED
    assert result.error_kind == kind.value
    assert "test-secret" not in result.message


def test_recognition_hints_progress_and_cancellation_are_request_scoped(audio_path: Path) -> None:
    events = []
    transport = FakeTransport(response(200, {"text": "hello"}))
    provider = OpenAITranscriptionProvider(api_key="test-secret", transport=transport)
    result = provider.transcribe(
        TranscriptionRequest(
            audio_path,
            recognition_hints=("OpenWhisper", "Yousuf"),
            progress=events.append,
        )
    )

    assert result.text == "hello"
    assert any(event.stage.value == "requesting" for event in events)
    assert events[-1].stage.value == "completed"
    assert b"Vocabulary: OpenWhisper, Yousuf" in transport.requests[0].body

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path, cancellation=cancelled))
    assert raised.value.kind is ProviderErrorKind.CANCELLED
    assert len(transport.requests) == 1


def test_core_adapter_forwards_session_cancellation_and_progress(audio_path: Path) -> None:
    cancellation = threading.Event()
    cancellation.set()
    events = []
    transport = FakeTransport(response(200, {"text": "must not be requested"}))
    adapter = CoreTranscriptionAdapter(
        OpenAITranscriptionProvider(api_key="test-secret", transport=transport),
        cancellation=cancellation,
        progress=events.append,
    )

    with pytest.raises(ProviderError) as raised:
        adapter.transcribe(audio_path)

    assert raised.value.kind is ProviderErrorKind.CANCELLED
    assert transport.requests == []
    assert events == []


def test_cleanup_context_is_explicit_and_never_implicit() -> None:
    transport = FakeTransport(response(200, {"message": {"content": [{"text": "edited"}]}}))
    provider = CohereCleanupProvider(api_key="test-secret", transport=transport)
    provider.cleanup(
        CleanupRequest(
            "draft",
            context=CleanupContext(ContextSource.SELECTED_TEXT, "selected text", "Writer"),
        )
    )
    message = json.loads(transport.requests[0].body)["messages"][1]["content"]
    assert "selected text" in message
    assert '<source name="selected_text">' in message

    multiple = CleanupContext.from_content(
        {"application": "Writer", "selected_text": "selection", "surrounding_text": "nearby"}
    )
    assert multiple is not None
    assert [entry.source.value for entry in multiple.entries] == [
        "application",
        "selected_text",
        "surrounding_text",
    ]


def test_stable_prefix_reconciliation_handles_overlap_and_final_alignment() -> None:
    reconciler = StablePrefixReconciler()
    assert reconciler.reconcile_chunk("مرحبا hello").insertion == "مرحبا hello"
    assert reconciler.reconcile_chunk("hello from OpenWhisper").insertion == "from OpenWhisper"
    assert reconciler.reconcile_final("مرحبا hello from OpenWhisper on Linux") == "on Linux"


@dataclass
class _Segment:
    text: str
    start: float = 0
    end: float = 1


@dataclass
class _Info:
    language: str = "en"
    duration: float = 1


class _StreamingModel:
    def __init__(self) -> None:
        self.values = iter(("hello world", "world again"))

    def transcribe(self, *_args, **_kwargs):
        return iter((_Segment(next(self.values)),)), _Info()


def test_faster_whisper_streaming_reconciles_chunk_tails(audio_path: Path) -> None:
    provider = FasterWhisperProvider(model_factory=lambda *_args: _StreamingModel())
    requests = (TranscriptionRequest(audio_path), TranscriptionRequest(audio_path))
    output = list(provider.transcribe_stream(requests))

    assert [item.text for item in output] == ["hello world", "again"]


class _Process:
    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


class _ExitedProcess(_Process):
    def poll(self) -> int:
        return 1


def test_local_qwen_gpu_startup_falls_back_to_cpu(tmp_path: Path) -> None:
    model_path = tmp_path / "Qwen3-4B-Q4_K_M.gguf"
    model_path.touch()
    commands: list[list[str]] = []

    def launch(command: list[str], _environment: dict[str, str]):
        commands.append(command)
        return _ExitedProcess() if len(commands) == 1 else _Process()

    server = LlamaServer(
        LlamaServerConfig(model_path=model_path, port=8123, experimental_gpu=True),
        transport=FakeTransport(response(200, {"status": "ok"})),
        process_factory=launch,
    )
    server.start()

    assert server.used_cpu_fallback
    assert commands[0][commands[0].index("--n-gpu-layers") + 1] == "-1"
    assert commands[1][commands[1].index("--n-gpu-layers") + 1] == "0"


def test_local_qwen_server_uses_private_token_and_non_thinking_request(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-local-child")
    model_path = tmp_path / "Qwen3-4B-Q4_K_M.gguf"
    model_path.touch()
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []
    transport = FakeTransport(
        response(200, {"status": "ok"}),
        response(200, {"choices": [{"message": {"content": "<think>x</think> edited"}}]}),
    )
    server = LlamaServer(
        LlamaServerConfig(model_path=model_path, port=8123),
        transport=transport,
        process_factory=lambda command, environment: (
            commands.append(command) or environments.append(environment) or _Process()
        ),
    )
    result = Qwen3LocalCleanupProvider(server).cleanup(CleanupRequest("draft"))

    command = commands[0]
    token = environments[0]["LLAMA_API_KEY"]
    assert "OPENAI_API_KEY" not in environments[0]
    request = transport.requests[-1]
    assert result.text == "edited"
    assert command[command.index("--n-gpu-layers") + 1] == "0"
    assert "--api-key" not in command
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert token not in repr(request)
    assert token not in str(ProviderError("local-qwen3", ProviderErrorKind.UNAVAILABLE, "failed"))
    assert json.loads(request.body)["chat_template_kwargs"] == {"enable_thinking": False}
