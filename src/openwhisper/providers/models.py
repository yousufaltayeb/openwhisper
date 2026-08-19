"""Central upstream model and endpoint identifiers.

Do not scatter model strings across adapters, the UI, or persisted settings.
This module is intentionally data-only so updating an upstream deprecation is a
small, reviewable change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

FASTER_WHISPER_DEFAULT_MODEL = "large-v3-turbo"
COHERE_LOCAL_ARABIC_MODEL = "CohereLabs/cohere-transcribe-arabic-07-2026"
QWEN3_EDITING_GGUF_REPOSITORY = "Qwen/Qwen3-4B-GGUF"
QWEN3_EDITING_GGUF_FILENAME = "Qwen3-4B-Q4_K_M.gguf"
QWEN3_EDITING_MODEL = "Qwen3-4B-GGUF-Q4_K_M"

COHERE_TRANSCRIBE_MODEL = "cohere-transcribe-arabic-07-2026"
OPENAI_TRANSCRIBE_MODEL = "gpt-transcribe"
GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
DEEPGRAM_TRANSCRIBE_MODEL = "nova-3"

COHERE_CLEANUP_MODEL = "command-a-03-2025"
OPENAI_CLEANUP_MODEL = "gpt-5-mini"
GROQ_CLEANUP_MODEL = "llama-3.3-70b-versatile"



class FasterWhisperModelGroup(StrEnum):
    MULTILINGUAL = "multilingual"
    ENGLISH_ONLY = "english-only"
    DISTILLED = "distilled"


@dataclass(frozen=True, slots=True)
class FasterWhisperModel:
    """Allowlisted model metadata shown by Settings and model storage."""

    id: str
    group: FasterWhisperModelGroup
    languages: str
    relative_speed: str
    relative_quality: str
    repo_id: str
    aliases: tuple[str, ...] = ()

    @property
    def supports_arabic(self) -> bool:
        return self.group is not FasterWhisperModelGroup.ENGLISH_ONLY


# The catalog intentionally lists canonical IDs only. ``turbo`` is retained
# as a read-only migration alias below, but is never rendered as a duplicate
# Settings choice. Repository revisions are pinned by the model manager's
# catalog map rather than accepting arbitrary Hugging Face IDs from IPC.
FASTER_WHISPER_CATALOG: tuple[FasterWhisperModel, ...] = (
    FasterWhisperModel(
        "tiny",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "fastest",
        "entry",
        "Systran/faster-whisper-tiny",
    ),
    FasterWhisperModel(
        "base",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "very fast",
        "good",
        "Systran/faster-whisper-base",
    ),
    FasterWhisperModel(
        "small",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "fast",
        "strong",
        "Systran/faster-whisper-small",
    ),
    FasterWhisperModel(
        "medium",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "balanced",
        "very strong",
        "Systran/faster-whisper-medium",
    ),
    FasterWhisperModel(
        "large-v1",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "slow",
        "high",
        "Systran/faster-whisper-large-v1",
    ),
    FasterWhisperModel(
        "large-v2",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "slow",
        "higher",
        "Systran/faster-whisper-large-v2",
    ),
    FasterWhisperModel(
        "large-v3",
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "slowest",
        "highest",
        "Systran/faster-whisper-large-v3",
        aliases=("large",),
    ),
    FasterWhisperModel(
        FASTER_WHISPER_DEFAULT_MODEL,
        FasterWhisperModelGroup.MULTILINGUAL,
        "99+ languages",
        "fast",
        "near-highest",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        aliases=("turbo",),
    ),
    FasterWhisperModel(
        "tiny.en",
        FasterWhisperModelGroup.ENGLISH_ONLY,
        "English",
        "fastest",
        "entry",
        "Systran/faster-whisper-tiny.en",
    ),
    FasterWhisperModel(
        "base.en",
        FasterWhisperModelGroup.ENGLISH_ONLY,
        "English",
        "very fast",
        "good",
        "Systran/faster-whisper-base.en",
    ),
    FasterWhisperModel(
        "small.en",
        FasterWhisperModelGroup.ENGLISH_ONLY,
        "English",
        "fast",
        "strong",
        "Systran/faster-whisper-small.en",
    ),
    FasterWhisperModel(
        "medium.en",
        FasterWhisperModelGroup.ENGLISH_ONLY,
        "English",
        "balanced",
        "very strong",
        "Systran/faster-whisper-medium.en",
    ),
    FasterWhisperModel(
        "distil-large-v2",
        FasterWhisperModelGroup.DISTILLED,
        "English",
        "fast",
        "high",
        "Systran/faster-distil-whisper-large-v2",
    ),
    FasterWhisperModel(
        "distil-medium.en",
        FasterWhisperModelGroup.DISTILLED,
        "English",
        "very fast",
        "strong",
        "Systran/faster-distil-whisper-medium.en",
    ),
    FasterWhisperModel(
        "distil-small.en",
        FasterWhisperModelGroup.DISTILLED,
        "English",
        "fastest",
        "good",
        "Systran/faster-distil-whisper-small.en",
    ),
    FasterWhisperModel(
        "distil-large-v3",
        FasterWhisperModelGroup.DISTILLED,
        "99+ languages",
        "fast",
        "high",
        "Systran/faster-distil-whisper-large-v3",
    ),
    FasterWhisperModel(
        "distil-large-v3.5",
        FasterWhisperModelGroup.DISTILLED,
        "99+ languages",
        "fast",
        "high",
        "distil-whisper/distil-large-v3.5-ct2",
    ),
)

FASTER_WHISPER_MODELS = tuple(model.id for model in FASTER_WHISPER_CATALOG)
FASTER_WHISPER_MODEL_BY_ID = {model.id: model for model in FASTER_WHISPER_CATALOG}
FASTER_WHISPER_MODEL_ALIASES = {
    alias: model.id for model in FASTER_WHISPER_CATALOG for alias in model.aliases
}

# Immutable Hugging Face revisions used by the engine-owned downloader. Model
# weights remain optional runtime downloads, but releases never follow moving
# repository branches.
FASTER_WHISPER_MODEL_REVISIONS = {
    "tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "large-v1": "b07c8d4be0be90092aa01a29c975077acb8d15c9",
    "large-v2": "f0fe81560cb8b68660e564f55dd99207059c092e",
    "large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    "tiny.en": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
    "base.en": "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
    "small.en": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    "medium.en": "a29b04bd15381511a9af671baec01072039215e3",
    "distil-large-v2": "fe9b404fc56de3f7c38606ef9ba6fd83526d05e4",
    "distil-medium.en": "80ddfce281f77766d8943d63109199fc8145dfa5",
    "distil-small.en": "ef77d90526ccd62cde3808ee70626a01e5cf83e4",
    "distil-large-v3": "c3058b475261292e64a0412df1d2681c06260fab",
    "distil-large-v3.5": "9793ccc07920e0f830e1dba0343efcdf0ef8c903",
}


def canonical_faster_whisper_model(model_id: str) -> str:
    """Resolve a historical alias without allowing arbitrary model IDs."""

    value = str(model_id).strip()
    return FASTER_WHISPER_MODEL_ALIASES.get(value, value)


def faster_whisper_model(model_id: str) -> FasterWhisperModel | None:
    return FASTER_WHISPER_MODEL_BY_ID.get(canonical_faster_whisper_model(model_id))
COHERE_LOCAL_ARABIC_MODELS = (COHERE_LOCAL_ARABIC_MODEL,)
COHERE_TRANSCRIBE_MODELS = (COHERE_TRANSCRIBE_MODEL,)
OPENAI_TRANSCRIBE_MODELS = (OPENAI_TRANSCRIBE_MODEL,)
GROQ_TRANSCRIBE_MODELS = (GROQ_TRANSCRIBE_MODEL, "whisper-large-v3")
DEEPGRAM_TRANSCRIBE_MODELS = (DEEPGRAM_TRANSCRIBE_MODEL,)

COHERE_CLEANUP_MODELS = (COHERE_CLEANUP_MODEL,)
OPENAI_CLEANUP_MODELS = (OPENAI_CLEANUP_MODEL,)
GROQ_CLEANUP_MODELS = (GROQ_CLEANUP_MODEL,)

COHERE_TRANSCRIBE_URL = "https://api.cohere.com/v2/audio/transcriptions"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEEPGRAM_TRANSCRIBE_URL = "https://api.deepgram.com/v1/listen"

COHERE_CHAT_URL = "https://api.cohere.com/v2/chat"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
