"""Central upstream model and endpoint identifiers.

Do not scatter model strings across adapters, the UI, or persisted settings.
This module is intentionally data-only so updating an upstream deprecation is a
small, reviewable change.
"""

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

FASTER_WHISPER_MODELS = (
    FASTER_WHISPER_DEFAULT_MODEL,
    "large-v3",
    "medium",
    "small",
)
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
