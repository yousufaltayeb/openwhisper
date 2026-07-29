"""HTTP adapters for supported cloud transcription APIs.

The adapters use their providers' documented wire protocols directly rather
than importing SDKs.  That keeps the production dependency surface small and
makes request/response contracts fully mockable in tests.
"""

from __future__ import annotations

import mimetypes
from typing import Any, ClassVar
from urllib.parse import urlencode

from ._shared import emit_progress, send_json, validate_request
from .connection import connection_result, minimal_silence_wav
from .contracts import (
    ConnectionTestResult,
    ProviderCapabilities,
    ProviderProgressStage,
    TranscriptionRequest,
    TranscriptResult,
    TranscriptSegment,
)
from .credentials import CredentialStore, resolve_api_key
from .errors import ProviderError, ProviderErrorKind
from .models import (
    COHERE_TRANSCRIBE_MODEL,
    COHERE_TRANSCRIBE_URL,
    DEEPGRAM_TRANSCRIBE_MODEL,
    DEEPGRAM_TRANSCRIBE_URL,
    GROQ_TRANSCRIBE_MODEL,
    GROQ_TRANSCRIBE_URL,
    OPENAI_TRANSCRIBE_MODEL,
    OPENAI_TRANSCRIBE_URL,
)
from .normalization import (
    normalize_segments,
    optional_number,
    transcript_result,
    wav_duration_seconds,
)
from .transport import HttpRequest, HttpTransport, UrllibTransport, encode_multipart


def _language_hint(value: str | None) -> str | None:
    if value is None or value.strip().casefold() == "auto":
        return None
    return value.strip()


def _recognition_prompt(request: TranscriptionRequest) -> str | None:
    """Combine an explicit prompt with bounded vocabulary recognition hints."""

    parts = [request.prompt.strip()] if request.prompt and request.prompt.strip() else []
    if request.recognition_hints:
        # This is reference vocabulary, not a user-context channel.  It is
        # compact on purpose so it cannot dominate the transcription prompt.
        parts.append("Vocabulary: " + ", ".join(request.recognition_hints))
    return "\n".join(parts) or None


class _CloudProvider:
    """Shared credential and transport ownership for cloud adapters."""

    name: ClassVar[str]
    model: str
    capabilities: ClassVar[ProviderCapabilities]

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        credentials: CredentialStore | None = None,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self._api_key = resolve_api_key(self.name, api_key=api_key, credentials=credentials)
        self._transport = transport or UrllibTransport()
        self._timeout_seconds = timeout_seconds

    def test_connection(self) -> ConnectionTestResult:
        """Make a real, content-free minimal request through this adapter."""

        def probe() -> None:
            with minimal_silence_wav() as audio_path:
                self.transcribe(TranscriptionRequest(audio_path=audio_path))  # type: ignore[attr-defined]

        return connection_result(self.name, self.model, probe)


class _MultipartTranscriptionProvider(_CloudProvider):
    endpoint: ClassVar[str]

    def transcribe(self, request: TranscriptionRequest) -> TranscriptResult:
        audio = validate_request(self.name, self.capabilities, request)
        fields = self._request_fields(request)
        body, content_type = encode_multipart(fields, file_path=request.audio_path, file_data=audio)
        payload = send_json(
            self.name,
            self._transport,
            HttpRequest(
                method="POST",
                url=self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": content_type,
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
                cancellation=request.cancellation,
            ),
            cancellation=request.cancellation,
            progress=request.progress,
        )
        result = self._response_to_result(payload, request)
        emit_progress(self.name, request.progress, ProviderProgressStage.COMPLETED, fraction=1)
        return result

    def _request_fields(self, request: TranscriptionRequest) -> dict[str, str]:
        fields = {"model": self.model}
        if language := _language_hint(request.language):
            fields["language"] = language
        if prompt := _recognition_prompt(request):
            fields["prompt"] = prompt
        return fields

    def _response_to_result(
        self, payload: dict[str, Any], request: TranscriptionRequest
    ) -> TranscriptResult:
        return transcript_result(
            provider=self.name,
            model=self.model,
            text=payload.get("text"),
            language=_language_hint(request.language),
            duration_seconds=payload.get("duration", wav_duration_seconds(request.audio_path)),
        )


class CohereTranscriptionProvider(_MultipartTranscriptionProvider):
    """Cohere V2 Audio Transcriptions adapter."""

    name = "cohere"
    endpoint = COHERE_TRANSCRIBE_URL
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=False,
        languages=None,
        timestamps=False,
        required_configuration=("COHERE_API_KEY",),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model=kwargs.pop("model", COHERE_TRANSCRIBE_MODEL), **kwargs)


class OpenAITranscriptionProvider(_MultipartTranscriptionProvider):
    """OpenAI Audio Transcriptions adapter for the current ``gpt-transcribe`` model."""

    name = "openai"
    endpoint = OPENAI_TRANSCRIBE_URL
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=False,
        languages=None,
        timestamps=False,
        required_configuration=("OPENAI_API_KEY",),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model=kwargs.pop("model", OPENAI_TRANSCRIBE_MODEL), **kwargs)

    def _request_fields(self, request: TranscriptionRequest) -> dict[str, str]:
        # The selected GPT transcription model uses JSON output rather than
        # verbose-JSON timestamp output. Do not promise timestamp capability.
        fields = super()._request_fields(request)
        fields["response_format"] = "json"
        return fields


class GroqTranscriptionProvider(_MultipartTranscriptionProvider):
    """Groq's OpenAI-compatible Whisper transcription adapter."""

    name = "groq"
    endpoint = GROQ_TRANSCRIBE_URL
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=False,
        languages=None,
        timestamps=True,
        required_configuration=("GROQ_API_KEY",),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model=kwargs.pop("model", GROQ_TRANSCRIBE_MODEL), **kwargs)

    def _request_fields(self, request: TranscriptionRequest) -> dict[str, str]:
        fields = super()._request_fields(request)
        fields["response_format"] = "verbose_json"
        if request.timestamps:
            # Multipart represents repeated parameter names as separate parts;
            # this dependency-free encoder takes a mapping, and Groq defaults
            # to segment timestamps for verbose_json.
            fields["timestamp_granularities[]"] = "segment"
        return fields

    def _response_to_result(
        self, payload: dict[str, Any], request: TranscriptionRequest
    ) -> TranscriptResult:
        segments = normalize_segments(
            self.name,
            payload.get("segments"),
            required=request.timestamps,
        )
        return transcript_result(
            provider=self.name,
            model=self.model,
            text=payload.get("text"),
            language=payload.get("language", _language_hint(request.language)),
            duration_seconds=payload.get("duration", wav_duration_seconds(request.audio_path)),
            segments=segments,
        )


class DeepgramTranscriptionProvider(_CloudProvider):
    """Deepgram prerecorded-audio ``/v1/listen`` adapter."""

    name = "deepgram"
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=False,
        languages=None,
        timestamps=True,
        required_configuration=("DEEPGRAM_API_KEY",),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model=kwargs.pop("model", DEEPGRAM_TRANSCRIBE_MODEL), **kwargs)

    def transcribe(self, request: TranscriptionRequest) -> TranscriptResult:
        audio = validate_request(self.name, self.capabilities, request)
        query: list[tuple[str, str]] = [("model", self.model), ("smart_format", "true")]
        if language := _language_hint(request.language):
            query.append(("language", language))
        # Word timings are returned in the normal prerecorded response.  The
        # flag requests utterances too when timestamped output is desired,
        # avoiding an unsupported synthetic timestamp promise.
        if request.timestamps:
            query.append(("utterances", "true"))
        query.extend(("keywords", hint) for hint in request.recognition_hints)
        content_type = (
            mimetypes.guess_type(request.audio_path.name)[0] or "application/octet-stream"
        )
        payload = send_json(
            self.name,
            self._transport,
            HttpRequest(
                method="POST",
                url=f"{DEEPGRAM_TRANSCRIBE_URL}?{urlencode(query, doseq=True)}",
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": content_type,
                },
                body=audio,
                timeout_seconds=self._timeout_seconds,
                cancellation=request.cancellation,
            ),
            cancellation=request.cancellation,
            progress=request.progress,
        )
        result = self._response_to_result(payload, request)
        emit_progress(self.name, request.progress, ProviderProgressStage.COMPLETED, fraction=1)
        return result

    def _response_to_result(
        self, payload: dict[str, Any], request: TranscriptionRequest
    ) -> TranscriptResult:
        try:
            alternative = payload["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                self.name,
                ProviderErrorKind.MALFORMED_RESPONSE,
                "deepgram response did not contain transcript text",
            ) from exc
        if not isinstance(alternative, dict):
            raise ProviderError(
                self.name,
                ProviderErrorKind.MALFORMED_RESPONSE,
                "deepgram response did not contain transcript text",
            )
        segments = _deepgram_segments(payload, alternative, required=request.timestamps)
        metadata = payload.get("metadata")
        duration = metadata.get("duration") if isinstance(metadata, dict) else None
        return transcript_result(
            provider=self.name,
            model=self.model,
            text=alternative.get("transcript"),
            language=_language_hint(request.language),
            duration_seconds=(
                duration if duration is not None else wav_duration_seconds(request.audio_path)
            ),
            segments=segments,
        )


def _deepgram_segments(
    payload: dict[str, Any], alternative: dict[str, Any], *, required: bool
) -> tuple[TranscriptSegment, ...]:
    raw_utterances = payload.get("results", {}).get("utterances")
    if isinstance(raw_utterances, list) and raw_utterances:
        return normalize_segments("deepgram", raw_utterances, required=required)

    raw_words = alternative.get("words")
    if raw_words is None and not required:
        return ()
    if not isinstance(raw_words, list):
        raise ProviderError(
            "deepgram",
            ProviderErrorKind.MALFORMED_RESPONSE,
            "deepgram response did not contain valid timestamp segments",
        )
    segments: list[TranscriptSegment] = []
    for word in raw_words:
        if not isinstance(word, dict):
            raise ProviderError(
                "deepgram",
                ProviderErrorKind.MALFORMED_RESPONSE,
                "deepgram response did not contain valid timestamp segments",
            )
        start = optional_number(word.get("start"))
        end = optional_number(word.get("end"))
        text = word.get("punctuated_word", word.get("word"))
        confidence = optional_number(word.get("confidence"))
        if start is None or end is None or not isinstance(text, str):
            raise ProviderError(
                "deepgram",
                ProviderErrorKind.MALFORMED_RESPONSE,
                "deepgram response did not contain valid timestamp segments",
            )
        try:
            segments.append(TranscriptSegment(text, start, end, confidence))
        except ValueError as exc:
            raise ProviderError(
                "deepgram",
                ProviderErrorKind.MALFORMED_RESPONSE,
                "deepgram response did not contain valid timestamp segments",
            ) from exc
    return tuple(segments)
