from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import FakeTransport, response

from openwhisper.providers import (
    CohereArabicLocalProvider,
    CohereTranscriptionProvider,
    DeepgramTranscriptionProvider,
    FasterWhisperProvider,
    GroqTranscriptionProvider,
    OpenAITranscriptionProvider,
    ProviderError,
    ProviderErrorKind,
    ProviderProgressStage,
    TranscriptionRequest,
)
from openwhisper.providers.transport import HttpResponse

CLOUD_PROVIDERS = (
    CohereTranscriptionProvider,
    OpenAITranscriptionProvider,
    GroqTranscriptionProvider,
    DeepgramTranscriptionProvider,
)


@pytest.mark.parametrize(
    ("provider_class", "payload", "language"),
    [
        (CohereTranscriptionProvider, {"text": "  مرحبا hello  "}, "ar"),
        (OpenAITranscriptionProvider, {"text": "  hello  "}, "en"),
        (
            GroqTranscriptionProvider,
            {
                "text": "مرحبا hello",
                "language": "ar",
                "duration": 1.25,
                "segments": [
                    {"text": "مرحبا", "start": 0, "end": 0.5},
                    {"text": "hello", "start": 0.5, "end": 1.25},
                ],
            },
            "ar",
        ),
    ],
)
def test_multipart_transcription_success_is_normalized(
    provider_class, payload, language, audio_path: Path
):
    transport = FakeTransport(response(200, payload))
    provider = provider_class(api_key="test-secret", transport=transport)

    result = provider.transcribe(
        TranscriptionRequest(
            audio_path,
            language=language,
            prompt="OpenWhisper",
            timestamps=provider.name == "groq",
        )
    )

    assert result.text == payload["text"].strip()
    assert result.provider == provider.name
    assert result.model == provider.model
    assert transport.requests[0].headers["Authorization"] == "Bearer test-secret"
    assert b'name="model"' in transport.requests[0].body
    assert b'name="file"' in transport.requests[0].body
    if provider.name == "groq":
        assert len(result.segments) == 2
        assert b'name="response_format"' in transport.requests[0].body
    if provider.name == "openai":
        assert b"json" in transport.requests[0].body


def test_deepgram_success_normalizes_words_and_query(audio_path: Path):
    transport = FakeTransport(
        response(
            200,
            {
                "metadata": {"duration": 1.0},
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {
                                    "transcript": "مرحبا hello",
                                    "words": [
                                        {"word": "مرحبا", "start": 0, "end": 0.5},
                                        {"word": "hello", "start": 0.5, "end": 1},
                                    ],
                                }
                            ]
                        }
                    ]
                },
            },
        )
    )
    result = DeepgramTranscriptionProvider(
        api_key="deepgram-secret", transport=transport
    ).transcribe(TranscriptionRequest(audio_path, language="ar", timestamps=True))

    assert result.text == "مرحبا hello"
    assert result.duration_seconds == 1.0
    assert [segment.text for segment in result.segments] == ["مرحبا", "hello"]
    request = transport.requests[0]
    assert request.headers["Authorization"] == "Token deepgram-secret"
    assert "model=nova-3" in request.url
    assert "utterances=true" in request.url


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ProviderErrorKind.AUTHENTICATION),
        (429, ProviderErrorKind.RATE_LIMIT),
        (400, ProviderErrorKind.INVALID_AUDIO),
    ],
)
def test_cloud_http_errors_are_mapped_without_credentials(audio_path: Path, status, kind):
    provider = CohereTranscriptionProvider(
        api_key="cohere-secret", transport=FakeTransport(HttpResponse(status, {}, b"secret echo"))
    )

    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path))

    assert raised.value.kind is kind
    assert "cohere-secret" not in str(raised.value)
    assert "secret echo" not in str(raised.value)


def test_cloud_timeout_invalid_audio_and_malformed_response(audio_path: Path, tmp_path: Path):
    timed_out = OpenAITranscriptionProvider(
        api_key="openai-secret", transport=FakeTransport(TimeoutError())
    )
    with pytest.raises(ProviderError) as raised:
        timed_out.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is ProviderErrorKind.TIMEOUT

    empty = tmp_path / "empty.wav"
    empty.touch()
    invalid = OpenAITranscriptionProvider(api_key="openai-secret", transport=FakeTransport())
    with pytest.raises(ProviderError) as raised:
        invalid.transcribe(TranscriptionRequest(empty))
    assert raised.value.kind is ProviderErrorKind.INVALID_AUDIO

    malformed = OpenAITranscriptionProvider(
        api_key="openai-secret", transport=FakeTransport(HttpResponse(200, {}, b"not json"))
    )
    with pytest.raises(ProviderError) as raised:
        malformed.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is ProviderErrorKind.MALFORMED_RESPONSE


@pytest.mark.parametrize("provider_class", CLOUD_PROVIDERS)
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ProviderErrorKind.AUTHENTICATION),
        (429, ProviderErrorKind.RATE_LIMIT),
    ],
)
def test_every_cloud_adapter_maps_authentication_and_rate_limits(
    provider_class, audio_path: Path, status, kind
):
    provider = provider_class(
        api_key="contract-secret",
        transport=FakeTransport(HttpResponse(status, {}, b"ignored response body")),
    )
    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is kind
    assert "contract-secret" not in str(raised.value)


@pytest.mark.parametrize("provider_class", CLOUD_PROVIDERS)
def test_every_cloud_adapter_maps_timeout(provider_class, audio_path: Path):
    provider = provider_class(api_key="contract-secret", transport=FakeTransport(TimeoutError()))
    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is ProviderErrorKind.TIMEOUT


@pytest.mark.parametrize("provider_class", CLOUD_PROVIDERS)
def test_every_cloud_adapter_rejects_invalid_audio(provider_class, tmp_path: Path):
    empty = tmp_path / f"{provider_class.__name__}.wav"
    empty.touch()
    provider = provider_class(api_key="contract-secret", transport=FakeTransport())
    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(empty))
    assert raised.value.kind is ProviderErrorKind.INVALID_AUDIO


@pytest.mark.parametrize("provider_class", CLOUD_PROVIDERS)
def test_every_cloud_adapter_rejects_malformed_response(provider_class, audio_path: Path):
    provider = provider_class(
        api_key="contract-secret",
        transport=FakeTransport(HttpResponse(200, {}, b"not-json")),
    )
    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is ProviderErrorKind.MALFORMED_RESPONSE


@dataclass
class _Segment:
    text: str
    start: float
    end: float
    avg_logprob: float = -0.1


@dataclass
class _Info:
    language: str = "ar"
    duration: float = 1.0


class _FasterWhisperModel:
    def transcribe(self, *_args, **_kwargs):
        return iter([_Segment("  مرحبا hello  ", 0, 1)]), _Info()


def test_faster_whisper_contract_and_streaming(audio_path: Path):
    loads: list[tuple[str, str, str]] = []

    def factory(model, device, compute_type):
        loads.append((model, device, compute_type))
        return _FasterWhisperModel()

    provider = FasterWhisperProvider(model_factory=factory)
    request = TranscriptionRequest(audio_path, language="ar", timestamps=True)
    result = provider.transcribe(request)

    assert provider.capabilities.streaming
    assert result.text == "مرحبا hello"
    assert result.language == "ar"
    assert len(result.segments) == 1
    assert list(provider.transcribe_stream([request]))[0].text == "مرحبا hello"
    assert len(loads) == 1


def test_faster_whisper_reports_model_loading_until_the_model_is_ready(audio_path: Path):
    stages = []

    def factory(_model, _device, _compute_type):
        assert [event.stage for event in stages] == [ProviderProgressStage.LOADING_MODEL]
        return _FasterWhisperModel()

    provider = FasterWhisperProvider(model_factory=factory)
    provider.transcribe(TranscriptionRequest(audio_path, progress=stages.append))

    assert [event.stage for event in stages] == [
        ProviderProgressStage.LOADING_MODEL,
        ProviderProgressStage.TRANSCRIBING,
        ProviderProgressStage.COMPLETED,
    ]


def test_optional_cohere_local_requires_explicit_arabic_or_english(audio_path: Path):
    provider = CohereArabicLocalProvider(
        pipeline_factory=lambda _model, _device: lambda _path: {"text": "مرحبا"}
    )

    with pytest.raises(ProviderError) as raised:
        provider.transcribe(TranscriptionRequest(audio_path))
    assert raised.value.kind is ProviderErrorKind.UNSUPPORTED_CAPABILITY

    assert provider.transcribe(TranscriptionRequest(audio_path, language="ar")).text == "مرحبا"
    assert provider.transcribe(TranscriptionRequest(audio_path, language="en")).text == "مرحبا"
