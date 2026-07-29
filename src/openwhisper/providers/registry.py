"""Provider catalog and one place to construct supported adapters."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec

from .bridges import CoreCleanupAdapter, CoreTranscriptionAdapter
from .cleanup import CohereCleanupProvider, GroqCleanupProvider, OpenAICleanupProvider
from .cloud import (
    CohereTranscriptionProvider,
    DeepgramTranscriptionProvider,
    GroqTranscriptionProvider,
    OpenAITranscriptionProvider,
)
from .contracts import CleanupProvider, ProviderCapabilities, TranscriptionProvider
from .credentials import CredentialStore
from .errors import ProviderError, ProviderErrorKind
from .local import CohereArabicLocalProvider, FasterWhisperProvider
from .models import (
    COHERE_CLEANUP_MODELS,
    COHERE_LOCAL_ARABIC_MODELS,
    COHERE_TRANSCRIBE_MODELS,
    DEEPGRAM_TRANSCRIBE_MODELS,
    FASTER_WHISPER_MODELS,
    GROQ_CLEANUP_MODELS,
    GROQ_TRANSCRIBE_MODELS,
    OPENAI_CLEANUP_MODELS,
    OPENAI_TRANSCRIBE_MODELS,
    QWEN3_EDITING_MODEL,
)
from .transport import HttpTransport


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Static metadata for settings UI and deterministic runtime routing."""

    id: str
    name: str
    description: str
    models: tuple[str, ...]
    capabilities: ProviderCapabilities
    needs_api_key: bool = False
    supports_cleanup: bool = False
    optional_dependency: str | None = None

    @property
    def supports_streaming(self) -> bool:
        return self.capabilities.streaming

    @property
    def available(self) -> bool:
        return self.optional_dependency is None or find_spec(self.optional_dependency) is not None

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return f"Install the optional {self.optional_dependency} dependency to enable this provider"


_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        id="faster-whisper",
        name="Faster-Whisper (local)",
        description="Private local transcription using Faster-Whisper.",
        models=FASTER_WHISPER_MODELS,
        capabilities=FasterWhisperProvider.capabilities,
        optional_dependency="faster_whisper",
    ),
    ProviderDefinition(
        id="cohere-local",
        name="Cohere Transcribe Arabic (local)",
        description="Optional local Arabic transcription model; audio stays on this device.",
        models=COHERE_LOCAL_ARABIC_MODELS,
        capabilities=CohereArabicLocalProvider.capabilities,
        optional_dependency="transformers",
    ),
    ProviderDefinition(
        id="local-qwen3",
        name="Qwen3 4B (local editing)",
        description="Optional private Qwen3 cleanup via the managed local GGUF pack.",
        models=(QWEN3_EDITING_MODEL,),
        capabilities=ProviderCapabilities(
            batch=False,
            streaming=False,
            languages=None,
            timestamps=False,
        ),
        supports_cleanup=True,
    ),
    ProviderDefinition(
        id="cohere",
        name="Cohere",
        description="Cohere hosted transcription and optional cleanup.",
        models=COHERE_TRANSCRIBE_MODELS,
        capabilities=CohereTranscriptionProvider.capabilities,
        needs_api_key=True,
        supports_cleanup=True,
    ),
    ProviderDefinition(
        id="openai",
        name="OpenAI",
        description="OpenAI hosted transcription and optional cleanup.",
        models=OPENAI_TRANSCRIBE_MODELS,
        capabilities=OpenAITranscriptionProvider.capabilities,
        needs_api_key=True,
        supports_cleanup=True,
    ),
    ProviderDefinition(
        id="groq",
        name="Groq",
        description="Fast hosted Whisper transcription and optional cleanup.",
        models=GROQ_TRANSCRIBE_MODELS,
        capabilities=GroqTranscriptionProvider.capabilities,
        needs_api_key=True,
        supports_cleanup=True,
    ),
    ProviderDefinition(
        id="deepgram",
        name="Deepgram",
        description="Hosted prerecorded-audio transcription.",
        models=DEEPGRAM_TRANSCRIBE_MODELS,
        capabilities=DeepgramTranscriptionProvider.capabilities,
        needs_api_key=True,
    ),
)
_BY_ID = {definition.id: definition for definition in _DEFINITIONS}
_ALIASES = {
    "faster_whisper": "faster-whisper",
    "fasterwhisper": "faster-whisper",
    "cohere_arabic_local": "cohere-local",
    "cohere-local-arabic": "cohere-local",
}


def canonical_provider_id(provider_id: str) -> str:
    normalized = provider_id.strip().casefold().replace("_", "-")
    normalized = _ALIASES.get(normalized, normalized)
    if normalized not in _BY_ID:
        raise ProviderError(
            normalized or "provider",
            ProviderErrorKind.CONFIGURATION,
            "Unknown transcription provider",
        )
    return normalized


class ProviderRouter:
    """Construct provider-contract adapters from canonical provider IDs.

    The router has no application state and does not make a network request.
    Injecting ``transport`` makes all resulting cloud adapters deterministic in
    tests.  Cloud credentials are resolved only when an individual cloud
    adapter is selected.
    """

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
        local_cleanup_provider: CleanupProvider | None = None,
    ) -> None:
        self.credentials = credentials or CredentialStore()
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.local_cleanup_provider = local_cleanup_provider

    def definitions(self) -> tuple[ProviderDefinition, ...]:
        return _DEFINITIONS

    def definition(self, provider_id: str) -> ProviderDefinition:
        return _BY_ID[canonical_provider_id(provider_id)]

    def transcription(
        self,
        provider_id: str,
        *,
        model: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
    ) -> TranscriptionProvider:
        """Return a request-contract transcription adapter; no I/O occurs here."""

        provider_id = canonical_provider_id(provider_id)
        common = {
            "credentials": self.credentials,
            "transport": self.transport,
            "timeout_seconds": self.timeout_seconds,
        }
        if provider_id == "faster-whisper":
            return FasterWhisperProvider(
                model=model or FASTER_WHISPER_MODELS[0],
                device=device,
                compute_type=compute_type,
            )
        if provider_id == "cohere-local":
            return CohereArabicLocalProvider(
                model=model or COHERE_LOCAL_ARABIC_MODELS[0],
                device=device,
            )
        if provider_id == "local-qwen3":
            raise ProviderError(
                provider_id,
                ProviderErrorKind.UNSUPPORTED_CAPABILITY,
                "local-qwen3 provides editing only, not transcription",
            )
        if provider_id == "cohere":
            return CohereTranscriptionProvider(model=model or COHERE_TRANSCRIBE_MODELS[0], **common)
        if provider_id == "openai":
            return OpenAITranscriptionProvider(model=model or OPENAI_TRANSCRIBE_MODELS[0], **common)
        if provider_id == "groq":
            return GroqTranscriptionProvider(model=model or GROQ_TRANSCRIBE_MODELS[0], **common)
        return DeepgramTranscriptionProvider(model=model or DEEPGRAM_TRANSCRIBE_MODELS[0], **common)

    def cleanup(self, provider_id: str, *, model: str | None = None) -> CleanupProvider:
        """Return a cleanup adapter for Cohere, OpenAI, or Groq."""

        provider_id = canonical_provider_id(provider_id)
        common = {
            "credentials": self.credentials,
            "transport": self.transport,
            "timeout_seconds": self.timeout_seconds,
        }
        if provider_id == "cohere":
            return CohereCleanupProvider(model=model or COHERE_CLEANUP_MODELS[0], **common)
        if provider_id == "openai":
            return OpenAICleanupProvider(model=model or OPENAI_CLEANUP_MODELS[0], **common)
        if provider_id == "groq":
            return GroqCleanupProvider(model=model or GROQ_CLEANUP_MODELS[0], **common)
        if provider_id == "local-qwen3":
            if self.local_cleanup_provider is None:
                raise ProviderError(
                    provider_id,
                    ProviderErrorKind.CONFIGURATION,
                    "Install and configure the managed local Qwen3 editing pack first",
                )
            return self.local_cleanup_provider
        raise ProviderError(
            provider_id,
            ProviderErrorKind.UNSUPPORTED_CAPABILITY,
            f"{provider_id} does not support transcript cleanup",
        )

    def core_transcription(
        self,
        provider_id: str,
        *,
        model: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = None,
        prompt: str | None = None,
        timestamps: bool = False,
    ) -> CoreTranscriptionAdapter:
        return CoreTranscriptionAdapter(
            self.transcription(
                provider_id,
                model=model,
                device=device,
                compute_type=compute_type,
            ),
            language=language,
            prompt=prompt,
            timestamps=timestamps,
        )

    def core_cleanup(
        self,
        provider_id: str,
        *,
        model: str | None = None,
        language_hint: str | None = None,
    ) -> CoreCleanupAdapter:
        return CoreCleanupAdapter(
            self.cleanup(provider_id, model=model),
            language_hint=language_hint,
        )

    # Explicit aliases make runtime wiring readable while preserving a concise
    # public surface for integrations that use ``transcription``/``cleanup``.
    build_transcription = transcription
    build_cleanup = cleanup
