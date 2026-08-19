"""Pluggable local and cloud transcription/cleanup providers."""

from .bridges import CoreCleanupAdapter, CoreTranscriptionAdapter
from .cleanup import (
    CohereCleanupProvider,
    GroqCleanupProvider,
    OpenAICleanupProvider,
)
from .cloud import (
    CohereTranscriptionProvider,
    DeepgramTranscriptionProvider,
    GroqTranscriptionProvider,
    OpenAITranscriptionProvider,
)
from .contracts import (
    CancellationToken,
    CleanupContext,
    CleanupContextEntry,
    CleanupMode,
    CleanupRequest,
    CleanupResult,
    ConnectionTestResult,
    ConnectionTestStatus,
    ContextSource,
    ProviderCapabilities,
    ProviderProgressEvent,
    ProviderProgressStage,
    TranscriptionRequest,
    TranscriptResult,
    TranscriptSegment,
)
from .credentials import (
    ENVIRONMENT_VARIABLES,
    CredentialStore,
    PortalCredentialBackend,
    QtSecretPortal,
    SecretPortal,
)
from .errors import ProviderError, ProviderErrorKind
from .local import CohereArabicLocalProvider, FasterWhisperProvider
from .local_editing import (
    LlamaServer,
    LlamaServerConfig,
    LocalEditingPackManager,
    LocalEditingPackStatus,
    Qwen3LocalCleanupProvider,
)
from .local_pack import CohereLocalPackManager, LocalPackStatus
from .model_manager import (
    ModelDownloadBusyError,
    ModelDownloadJob,
    ModelManager,
    ModelState,
    ModelStatus,
)
from .registry import ProviderDefinition, ProviderRouter, canonical_provider_id
from .streaming import ReconciledText, StablePrefixReconciler

__all__ = [
    "CancellationToken",
    "CleanupContext",
    "CleanupContextEntry",
    "CleanupMode",
    "CleanupRequest",
    "CleanupResult",
    "CohereArabicLocalProvider",
    "CohereCleanupProvider",
    "CohereLocalPackManager",
    "CohereTranscriptionProvider",
    "ConnectionTestResult",
    "ConnectionTestStatus",
    "ContextSource",
    "CoreCleanupAdapter",
    "CoreTranscriptionAdapter",
    "CredentialStore",
    "DeepgramTranscriptionProvider",
    "ENVIRONMENT_VARIABLES",
    "FasterWhisperProvider",
    "GroqCleanupProvider",
    "GroqTranscriptionProvider",
    "LlamaServer",
    "LlamaServerConfig",
    "LocalEditingPackManager",
    "LocalEditingPackStatus",
    "LocalPackStatus",
    "ModelDownloadBusyError",
    "ModelDownloadJob",
    "ModelManager",
    "ModelState",
    "ModelStatus",
    "OpenAICleanupProvider",
    "OpenAITranscriptionProvider",
    "ProviderCapabilities",
    "ProviderProgressEvent",
    "ProviderProgressStage",
    "ProviderDefinition",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderRouter",
    "PortalCredentialBackend",
    "Qwen3LocalCleanupProvider",
    "QtSecretPortal",
    "SecretPortal",
    "ReconciledText",
    "StablePrefixReconciler",
    "TranscriptResult",
    "TranscriptSegment",
    "TranscriptionRequest",
    "canonical_provider_id",
]
