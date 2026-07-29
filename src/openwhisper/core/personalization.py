"""Privacy-preserving personal dictation features.

This module deliberately has no desktop, provider, or Qt dependency.  It is
the stable contract used by the UI and by a future platform adapter.  In
particular, :class:`DictationContext` keeps the *policy* and the actual text
separate so history and diagnostic code can never accidentally persist context
captured from another application.
"""

from __future__ import annotations

import csv
import difflib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from io import StringIO
from pathlib import Path

SCHEMA_VERSION = 1


class ContextSource(StrEnum):
    """Optional sources of desktop context; all are opt-in per mode."""

    APPLICATION = "application"
    SELECTED_TEXT = "selected_text"
    SURROUNDING_TEXT = "surrounding_text"
    RECENT_CLIPBOARD = "recent_clipboard"


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Consent for context collection in one mode.

    Context is disabled by default.  ``allow_cloud`` is intentionally separate
    from source selection: a person can use selected text for a local editing
    pack without ever making it eligible for a cloud request.
    """

    enabled_sources: frozenset[ContextSource] = frozenset()
    allow_cloud: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "enabled_sources",
            frozenset(ContextSource(source) for source in self.enabled_sources),
        )
        if not self.enabled_sources and self.allow_cloud:
            raise ValueError("cloud context requires at least one enabled source")

    @property
    def is_enabled(self) -> bool:
        return bool(self.enabled_sources)

    def permits(self, source: ContextSource, *, cloud: bool = False) -> bool:
        return source in self.enabled_sources and (not cloud or self.allow_cloud)

    def badge(self, *, cloud: bool = False) -> str:
        if not self.enabled_sources:
            return "Context off"
        source_names = ", ".join(source.replace("_", " ") for source in self.enabled_sources)
        if cloud:
            return f"Cloud context: {source_names}" if self.allow_cloud else "Cloud context blocked"
        return f"Local context: {source_names}"


@dataclass(frozen=True, slots=True)
class DictationContext:
    """Potential desktop context.  Never serialize this object to history/logs."""

    application_name: str | None = None
    selected_text: str | None = None
    surrounding_text: str | None = None
    recent_clipboard: str | None = None

    def content_for(
        self, policy: ContextPolicy, *, cloud: bool = False
    ) -> dict[ContextSource, str]:
        values = {
            ContextSource.APPLICATION: self.application_name,
            ContextSource.SELECTED_TEXT: self.selected_text,
            ContextSource.SURROUNDING_TEXT: self.surrounding_text,
            ContextSource.RECENT_CLIPBOARD: self.recent_clipboard,
        }
        return {
            source: value.strip()
            for source, value in values.items()
            if value and policy.permits(source, cloud=cloud)
        }

    def history_metadata(self, policy: ContextPolicy) -> tuple[str, ...]:
        """Return source names only; content must never reach history."""

        return tuple(sorted(source.value for source in policy.enabled_sources))


class CleanupStyle(StrEnum):
    RAW = "raw"
    CLEAN = "clean"
    FORMAL = "formal"
    MESSAGE = "message"
    EMAIL = "email"
    NOTE = "note"
    SMART = "smart"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ActivationRule:
    """A conservative app/site matcher for automatic mode routing.

    Patterns are plain case-insensitive substrings, not regular expressions.
    This keeps a malformed user rule from preventing dictation from starting.
    """

    application_pattern: str = ""
    site_pattern: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.application_pattern.strip() and not self.site_pattern.strip():
            raise ValueError("an activation rule needs an application or site pattern")

    def matches(self, *, application: str | None = None, site: str | None = None) -> bool:
        if not self.enabled:
            return False
        application_match = (
            not self.application_pattern
            or self.application_pattern.casefold() in (application or "").casefold()
        )
        site_match = (
            not self.site_pattern or self.site_pattern.casefold() in (site or "").casefold()
        )
        return application_match and site_match


@dataclass(frozen=True, slots=True)
class ModeDefinition:
    """A named, independently configurable dictation workflow."""

    id: str
    name: str
    cleanup_style: CleanupStyle = CleanupStyle.RAW
    transcription_provider: str | None = None
    transcription_model: str | None = None
    language: str = "auto"
    cleanup_provider: str | None = None
    cleanup_model: str | None = None
    custom_instruction: str | None = None
    shortcut: str | None = None
    live_insertion: bool = False
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    activation_rules: tuple[ActivationRule, ...] = ()
    built_in: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.id):
            raise ValueError("mode id must contain lowercase letters, numbers, _ or -")
        if not self.name.strip():
            raise ValueError("mode name cannot be empty")
        object.__setattr__(self, "cleanup_style", CleanupStyle(self.cleanup_style))
        object.__setattr__(self, "activation_rules", tuple(self.activation_rules))
        if (
            self.cleanup_style is CleanupStyle.CUSTOM
            and not (self.custom_instruction or "").strip()
        ):
            raise ValueError("a custom mode needs cleanup instructions")


def _builtin_mode(
    identifier: str, name: str, style: CleanupStyle, **values: object
) -> ModeDefinition:
    return ModeDefinition(id=identifier, name=name, cleanup_style=style, built_in=True, **values)


BUILTIN_MODES: tuple[ModeDefinition, ...] = (
    _builtin_mode("raw", "Raw", CleanupStyle.RAW),
    _builtin_mode("clean", "Clean", CleanupStyle.CLEAN),
    _builtin_mode("formal-msa", "Formal / MSA", CleanupStyle.FORMAL, language="ar"),
    _builtin_mode("message", "Message", CleanupStyle.MESSAGE),
    _builtin_mode("email", "Email", CleanupStyle.EMAIL),
    _builtin_mode("note", "Note formatting", CleanupStyle.NOTE),
    _builtin_mode("smart", "Smart", CleanupStyle.SMART),
)


class ModeRouter:
    """Resolve an explicitly selected mode, then a matching activation rule."""

    def __init__(self, modes: Iterable[ModeDefinition] = BUILTIN_MODES) -> None:
        self._modes = tuple(modes)
        if not self._modes:
            raise ValueError("at least one mode is required")
        self._by_id = {mode.id: mode for mode in self._modes}
        if len(self._by_id) != len(self._modes):
            raise ValueError("mode ids must be unique")

    @property
    def modes(self) -> tuple[ModeDefinition, ...]:
        return self._modes

    def route(
        self,
        *,
        selected_id: str | None = None,
        application: str | None = None,
        site: str | None = None,
    ) -> ModeDefinition:
        if selected_id is not None:
            try:
                return self._by_id[selected_id]
            except KeyError as error:
                raise ValueError(f"unknown mode: {selected_id}") from error
        for mode in self._modes:
            if any(
                rule.matches(application=application, site=site) for rule in mode.activation_rules
            ):
                return mode
        return self._by_id.get("raw", self._modes[0])


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    id: str
    written_form: str
    spoken_forms: tuple[str, ...]
    language: str = "auto"
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("vocabulary id cannot be empty")
        if not self.written_form.strip():
            raise ValueError("written form cannot be empty")
        forms = tuple(form.strip() for form in self.spoken_forms if form.strip())
        if not forms:
            raise ValueError("at least one spoken form is required")
        object.__setattr__(self, "spoken_forms", forms)


@dataclass(frozen=True, slots=True)
class Snippet:
    id: str
    trigger: str
    expansion: str
    language: str = "auto"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.trigger.strip() or not self.expansion:
            raise ValueError("a snippet requires an id, trigger, and expansion")


def apply_vocabulary(text: str, entries: Iterable[VocabularyEntry]) -> str:
    """Apply deterministic whole-phrase corrections without touching substrings."""

    result = text
    for entry in entries:
        flags = 0 if entry.case_sensitive else re.IGNORECASE
        for spoken in sorted(entry.spoken_forms, key=len, reverse=True):
            # ``\w`` includes Arabic letters, so this is conservative for both
            # Arabic and English and does not replace a term inside a larger word.
            expression = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)", flags)
            result = expression.sub(entry.written_form, result)
    return result


def apply_snippets(text: str, snippets: Iterable[Snippet]) -> str:
    result = text
    for snippet in snippets:
        if not snippet.enabled:
            continue
        expression = re.compile(rf"(?<!\w){re.escape(snippet.trigger)}(?!\w)", re.IGNORECASE)
        result = expression.sub(snippet.expansion, result)
    return result


_SPOKEN_TOKENS: tuple[tuple[str, str], ...] = (
    ("new paragraph", "\n\n"),
    ("فقرة جديدة", "\n\n"),
    ("bullet point", "\n• "),
    ("نقطة تعداد", "\n• "),
    ("new line", "\n"),
    ("سطر جديد", "\n"),
    ("question mark", "?"),
    ("علامة استفهام", "؟"),
    ("exclamation mark", "!"),
    ("علامة تعجب", "!"),
    ("colon", ":"),
    ("نقطتان", ":"),
    ("comma", ","),
    ("فاصلة", "،"),
    ("period", "."),
    ("نقطة", "."),
)
_FILLERS = ("um", "uh", "erm", "يعني", "ااا")


def smart_format(text: str, *, remove_fillers: bool = True) -> str:
    """Format spoken punctuation and simple self-corrections deterministically.

    It intentionally avoids generative rewriting.  The formatter never
    translates, reorders Arabic/English text, or guesses at a user's meaning.
    """

    output = text.strip()
    if not output:
        return ""
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group())
        return f"__OW_PROTECTED_{len(protected) - 1}__"

    # Formatting spaces around dictated punctuation must not corrupt an email
    # address or URL supplied by a snippet/vocabulary entry.
    output = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", protect, output)
    output = re.sub(r"\bhttps?://[^\s]+", protect, output)
    for spoken, token in _SPOKEN_TOKENS:
        output = re.sub(rf"(?<!\w){re.escape(spoken)}(?!\w)", token, output, flags=re.IGNORECASE)
    if remove_fillers:
        for filler in _FILLERS:
            output = re.sub(
                rf"(?<!\w){re.escape(filler)}(?!\w)\s*", "", output, flags=re.IGNORECASE
            )
    # A deliberate correction command is safe only for the immediately
    # preceding token.  Do not attempt speculative sentence-level backtracking.
    correction = r"(?:delete that|scratch that|احذف ذلك|امسح ذلك)"
    output = re.sub(rf"\S+\s+{correction}(?!\w)", "", output, flags=re.IGNORECASE)
    output = re.sub(r"[ \t]+", " ", output)
    output = re.sub(r"\s*\n\s*", "\n", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    output = re.sub(r"\s+([,.;:!?،؟])", r"\1", output)
    output = re.sub(r"([,.;:!?،؟])(?=[^\s\n])", r"\1 ", output)

    output = output.strip()

    # Capitalize English starts but leave Arabic and mixed-script words intact.
    def capitalize(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2).upper()

    output = re.sub(r"(^|[.!?]\s+|\n)([a-z])", capitalize, output)
    for index, value in enumerate(protected):
        output = output.replace(f"__OW_PROTECTED_{index}__", value)
    return output.strip()


class TransformKind(StrEnum):
    POLISH = "polish"
    FORMAL = "formal"
    BULLETS = "bullets"
    TRANSLATE = "translate"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class TransformDefinition:
    id: str
    name: str
    kind: TransformKind = TransformKind.CUSTOM
    instruction: str = ""
    target_language: str | None = None
    enabled: bool = True
    built_in: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.name.strip():
            raise ValueError("a transform requires an id and name")
        object.__setattr__(self, "kind", TransformKind(self.kind))
        if self.kind is TransformKind.CUSTOM and not self.instruction.strip():
            raise ValueError("a custom transform requires instructions")
        if self.kind is TransformKind.TRANSLATE and not (self.target_language or "").strip():
            raise ValueError("a translation transform requires a target language")


BUILTIN_TRANSFORMS: tuple[TransformDefinition, ...] = (
    TransformDefinition("polish", "Polish", TransformKind.POLISH, built_in=True),
    TransformDefinition("formal-tone", "Formal tone", TransformKind.FORMAL, built_in=True),
    TransformDefinition("make-list", "Make a list", TransformKind.BULLETS, built_in=True),
    TransformDefinition(
        "translate-en",
        "Translate to English",
        TransformKind.TRANSLATE,
        target_language="English",
        built_in=True,
    ),
)


@dataclass(frozen=True, slots=True)
class TransformPreview:
    transform: TransformDefinition
    original: str
    proposed: str
    diff: str

    @property
    def changed(self) -> bool:
        return self.original != self.proposed


class TransformEngine:
    """Preview transformations before they are applied, with local undo/redo."""

    def __init__(
        self, custom_runner: Callable[[str, TransformDefinition], str] | None = None
    ) -> None:
        self._custom_runner = custom_runner
        self._undo: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []

    def preview(self, text: str, transform: TransformDefinition) -> TransformPreview:
        if not text.strip():
            raise ValueError("select text before applying a transform")
        proposed = self._run(text, transform)
        diff = "\n".join(
            difflib.unified_diff(
                text.splitlines(),
                proposed.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        return TransformPreview(transform, text, proposed, diff)

    def apply(self, preview: TransformPreview) -> str:
        if preview.changed:
            self._undo.append((preview.original, preview.proposed))
            self._redo.clear()
        return preview.proposed

    def undo(self, current_text: str) -> str:
        if not self._undo:
            return current_text
        original, applied = self._undo.pop()
        if current_text != applied:
            # An editor changed after the preview was applied.  Do not replace
            # unrelated text; preserve it and retain the undo entry.
            self._undo.append((original, applied))
            return current_text
        self._redo.append((original, applied))
        return original

    def redo(self, current_text: str) -> str:
        if not self._redo:
            return current_text
        original, applied = self._redo.pop()
        if current_text != original:
            self._redo.append((original, applied))
            return current_text
        self._undo.append((original, applied))
        return applied

    def _run(self, text: str, transform: TransformDefinition) -> str:
        if transform.kind is TransformKind.POLISH:
            return smart_format(text)
        if transform.kind is TransformKind.FORMAL:
            return smart_format(text).replace("!", ".")
        if transform.kind is TransformKind.BULLETS:
            lines = [
                line.strip(" -•\t")
                for line in re.split(r"(?:\n+|(?<=[.!؟])\s+)", text)
                if line.strip()
            ]
            return "\n".join(f"• {line}" for line in lines)
        if self._custom_runner is None:
            raise ValueError(f"{transform.name} needs an editing provider")
        return self._custom_runner(text, transform).strip()


@dataclass(frozen=True, slots=True)
class TextOutput:
    text: str


@dataclass(frozen=True, slots=True)
class KeyAction:
    key: str


@dataclass(frozen=True, slots=True)
class ConfigurationProposal:
    key: str
    value: str
    summary: str
    requires_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    query: str
    limit: int = 20


CommandOutcome = TextOutput | KeyAction | ConfigurationProposal | HistoryQuery


class CommandValidator:
    """Parse a deliberately small command vocabulary without side effects."""

    _copy = {"copy last transcript", "انسخ آخر نص"}
    _paste = {"paste last transcript", "الصق آخر نص"}
    _enter = {"press enter", "اضغط إدخال"}
    _history_prefixes = ("search history ", "ابحث في السجل ")
    _mode_prefixes = ("switch mode to ", "غيّر الوضع إلى ")

    def validate(self, command: str, *, selected_text: str = "") -> CommandOutcome:
        normalized = " ".join(command.casefold().split())
        if normalized in self._copy:
            return TextOutput("__copy_last_transcript__")
        if normalized in self._paste:
            return TextOutput("__paste_last_transcript__")
        if normalized in self._enter:
            return KeyAction("Enter")
        for prefix in self._history_prefixes:
            if normalized.startswith(prefix):
                query = command[len(prefix) :].strip()
                if not query:
                    raise ValueError("history search needs words to search for")
                return HistoryQuery(query)
        for prefix in self._mode_prefixes:
            if normalized.startswith(prefix):
                mode = command[len(prefix) :].strip()
                if not mode:
                    raise ValueError("mode changes need a mode name")
                return ConfigurationProposal(
                    "active_mode", mode, f"Switch dictation mode to {mode}"
                )
        if normalized.startswith("rewrite selection "):
            if not selected_text.strip():
                raise ValueError("select text before asking to rewrite it")
            return TextOutput(command[len("rewrite selection ") :].strip())
        if normalized.startswith("generate "):
            prompt = command[len("generate ") :].strip()
            if not prompt:
                raise ValueError("generation needs an instruction")
            return TextOutput(prompt)
        raise ValueError("unknown command")


class SQLitePersonalizationStore:
    """Small local repository for user-authored personalization items.

    API credentials, recorded context, and transcripts never appear in this
    database.  JSON and CSV imports are parsed before a transaction begins, so
    a bad import cannot leave a partial vocabulary behind.
    """

    _upsert_vocabulary = "\n".join(
        (
            "INSERT INTO vocabulary(",
            "    id, written_form, spoken_forms, language, case_sensitive",
            ") VALUES (?, ?, ?, ?, ?)",
            "ON CONFLICT(id) DO UPDATE SET",
            "    written_form=excluded.written_form,",
            "    spoken_forms=excluded.spoken_forms,",
            "    language=excluded.language,",
            "    case_sensitive=excluded.case_sensitive",
        )
    )
    _upsert_snippet = "\n".join(
        (
            "INSERT INTO snippets(id, trigger, expansion, language, enabled) VALUES(?, ?, ?, ?, ?)",
            "ON CONFLICT(id) DO UPDATE SET trigger=excluded.trigger,",
            "    expansion=excluded.expansion, language=excluded.language,",
            "    enabled=excluded.enabled",
        )
    )
    _upsert_transform = "\n".join(
        (
            "INSERT INTO transforms(id, name, kind, instruction, target_language, enabled)",
            "VALUES(?, ?, ?, ?, ?, ?)",
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,",
            "    instruction=excluded.instruction, target_language=excluded.target_language,",
            "    enabled=excluded.enabled",
        )
    )

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS modes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activation_rules (
                    id TEXT PRIMARY KEY, mode_id TEXT NOT NULL,
                    application_pattern TEXT NOT NULL, site_pattern TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    FOREIGN KEY(mode_id) REFERENCES modes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS vocabulary (
                    id TEXT PRIMARY KEY, written_form TEXT NOT NULL, spoken_forms TEXT NOT NULL,
                    language TEXT NOT NULL, case_sensitive INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snippets (
                    id TEXT PRIMARY KEY, trigger TEXT NOT NULL, expansion TEXT NOT NULL,
                    language TEXT NOT NULL, enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transforms (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    target_language TEXT, enabled INTEGER NOT NULL
                );
                """
            )
            self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def modes(self) -> tuple[ModeDefinition, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM modes ORDER BY name COLLATE NOCASE"
            ).fetchall()
        saved = {item.id: item for item in (_mode_from_json(row["payload"]) for row in rows)}
        builtins = tuple(saved.pop(mode.id, mode) for mode in BUILTIN_MODES)
        return builtins + tuple(saved.values())

    def save_mode(self, mode: ModeDefinition) -> ModeDefinition:
        payload = _mode_to_json(mode)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO modes(id, name, payload) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, payload=excluded.payload",
                (mode.id, mode.name, payload),
            )
            self._connection.execute("DELETE FROM activation_rules WHERE mode_id = ?", (mode.id,))
            self._connection.executemany(
                "INSERT INTO activation_rules(\n"
                "    id, mode_id, application_pattern, site_pattern, enabled\n)"
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        str(uuid.uuid4()),
                        mode.id,
                        rule.application_pattern,
                        rule.site_pattern,
                        rule.enabled,
                    )
                    for rule in mode.activation_rules
                ),
            )
        return mode

    def delete_mode(self, identifier: str) -> None:
        if identifier in {mode.id for mode in BUILTIN_MODES}:
            raise ValueError("built-in modes cannot be deleted")
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM modes WHERE id = ?", (identifier,))

    def vocabulary(self) -> tuple[VocabularyEntry, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM vocabulary ORDER BY written_form COLLATE NOCASE"
            ).fetchall()
        return tuple(
            VocabularyEntry(
                row["id"],
                row["written_form"],
                tuple(json.loads(row["spoken_forms"])),
                row["language"],
                bool(row["case_sensitive"]),
            )
            for row in rows
        )

    def save_vocabulary(self, entry: VocabularyEntry) -> VocabularyEntry:
        with self._lock, self._connection:
            self._connection.execute(
                self._upsert_vocabulary,
                (
                    entry.id,
                    entry.written_form,
                    json.dumps(entry.spoken_forms),
                    entry.language,
                    entry.case_sensitive,
                ),
            )
        return entry

    def delete_vocabulary(self, identifier: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM vocabulary WHERE id = ?", (identifier,))

    def snippets(self) -> tuple[Snippet, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM snippets ORDER BY trigger COLLATE NOCASE"
            ).fetchall()
        return tuple(
            Snippet(
                row["id"], row["trigger"], row["expansion"], row["language"], bool(row["enabled"])
            )
            for row in rows
        )

    def save_snippet(self, snippet: Snippet) -> Snippet:
        with self._lock, self._connection:
            self._connection.execute(
                self._upsert_snippet,
                (snippet.id, snippet.trigger, snippet.expansion, snippet.language, snippet.enabled),
            )
        return snippet

    def delete_snippet(self, identifier: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM snippets WHERE id = ?", (identifier,))

    def transforms(self) -> tuple[TransformDefinition, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM transforms ORDER BY name COLLATE NOCASE"
            ).fetchall()
        saved = tuple(
            TransformDefinition(
                row["id"],
                row["name"],
                row["kind"],
                row["instruction"],
                row["target_language"],
                bool(row["enabled"]),
            )
            for row in rows
        )
        builtin_ids = {transform.id for transform in saved}
        return (
            tuple(transform for transform in BUILTIN_TRANSFORMS if transform.id not in builtin_ids)
            + saved
        )

    def save_transform(self, transform: TransformDefinition) -> TransformDefinition:
        with self._lock, self._connection:
            self._connection.execute(
                self._upsert_transform,
                (
                    transform.id,
                    transform.name,
                    transform.kind.value,
                    transform.instruction,
                    transform.target_language,
                    transform.enabled,
                ),
            )
        return transform

    def delete_transform(self, identifier: str) -> None:
        if identifier in {transform.id for transform in BUILTIN_TRANSFORMS}:
            raise ValueError("built-in transforms cannot be deleted")
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM transforms WHERE id = ?", (identifier,))

    def export_vocabulary(self, *, format: str = "json") -> str:
        entries = self.vocabulary()
        if format == "json":
            return json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2)
        if format == "csv":
            stream = StringIO()
            writer = csv.DictWriter(
                stream,
                fieldnames=("id", "written_form", "spoken_forms", "language", "case_sensitive"),
            )
            writer.writeheader()
            for entry in entries:
                writer.writerow({**asdict(entry), "spoken_forms": " | ".join(entry.spoken_forms)})
            return stream.getvalue()
        raise ValueError("format must be json or csv")

    def import_vocabulary(
        self, source: str, *, format: str = "json"
    ) -> tuple[VocabularyEntry, ...]:
        if format == "json":
            raw = json.loads(source)
            if not isinstance(raw, list):
                raise ValueError("vocabulary JSON must be a list")
            entries = tuple(
                VocabularyEntry(
                    str(item.get("id") or uuid.uuid4()),
                    str(item["written_form"]),
                    tuple(item["spoken_forms"]),
                    str(item.get("language", "auto")),
                    bool(item.get("case_sensitive", False)),
                )
                for item in raw
            )
        elif format == "csv":
            entries = tuple(
                VocabularyEntry(
                    row.get("id") or str(uuid.uuid4()),
                    row["written_form"],
                    tuple(part.strip() for part in row["spoken_forms"].split("|") if part.strip()),
                    row.get("language") or "auto",
                    row.get("case_sensitive", "").casefold() in {"1", "true", "yes"},
                )
                for row in csv.DictReader(StringIO(source))
            )
        else:
            raise ValueError("format must be json or csv")
        with self._lock, self._connection:
            for entry in entries:
                self._connection.execute(
                    self._upsert_vocabulary,
                    (
                        entry.id,
                        entry.written_form,
                        json.dumps(entry.spoken_forms),
                        entry.language,
                        entry.case_sensitive,
                    ),
                )
        return entries

    def export_snippets(self, *, format: str = "json") -> str:
        snippets = self.snippets()
        if format == "json":
            return json.dumps(
                [asdict(snippet) for snippet in snippets], ensure_ascii=False, indent=2
            )
        if format == "csv":
            stream = StringIO()
            writer = csv.DictWriter(
                stream,
                fieldnames=("id", "trigger", "expansion", "language", "enabled"),
            )
            writer.writeheader()
            writer.writerows(asdict(snippet) for snippet in snippets)
            return stream.getvalue()
        raise ValueError("format must be json or csv")

    def import_snippets(self, source: str, *, format: str = "json") -> tuple[Snippet, ...]:
        if format == "json":
            raw = json.loads(source)
            if not isinstance(raw, list):
                raise ValueError("snippet JSON must be a list")
            snippets = tuple(
                Snippet(
                    str(item.get("id") or uuid.uuid4()),
                    str(item["trigger"]),
                    str(item["expansion"]),
                    str(item.get("language", "auto")),
                    bool(item.get("enabled", True)),
                )
                for item in raw
            )
        elif format == "csv":
            snippets = tuple(
                Snippet(
                    row.get("id") or str(uuid.uuid4()),
                    row["trigger"],
                    row["expansion"],
                    row.get("language") or "auto",
                    row.get("enabled", "true").casefold() in {"1", "true", "yes"},
                )
                for row in csv.DictReader(StringIO(source))
            )
        else:
            raise ValueError("format must be json or csv")
        with self._lock, self._connection:
            for snippet in snippets:
                self._connection.execute(
                    self._upsert_snippet,
                    (
                        snippet.id,
                        snippet.trigger,
                        snippet.expansion,
                        snippet.language,
                        snippet.enabled,
                    ),
                )
        return snippets


def _mode_to_json(mode: ModeDefinition) -> str:
    return json.dumps(
        {
            "id": mode.id,
            "name": mode.name,
            "cleanup_style": mode.cleanup_style.value,
            "transcription_provider": mode.transcription_provider,
            "transcription_model": mode.transcription_model,
            "language": mode.language,
            "cleanup_provider": mode.cleanup_provider,
            "cleanup_model": mode.cleanup_model,
            "custom_instruction": mode.custom_instruction,
            "shortcut": mode.shortcut,
            "live_insertion": mode.live_insertion,
            "context_sources": sorted(
                source.value for source in mode.context_policy.enabled_sources
            ),
            "allow_cloud_context": mode.context_policy.allow_cloud,
            "activation_rules": [asdict(rule) for rule in mode.activation_rules],
            "built_in": mode.built_in,
        },
        ensure_ascii=False,
    )


def _mode_from_json(value: str) -> ModeDefinition:
    raw = json.loads(value)
    return ModeDefinition(
        id=raw["id"],
        name=raw["name"],
        cleanup_style=raw.get("cleanup_style", "raw"),
        transcription_provider=raw.get("transcription_provider"),
        transcription_model=raw.get("transcription_model"),
        language=raw.get("language", "auto"),
        cleanup_provider=raw.get("cleanup_provider"),
        cleanup_model=raw.get("cleanup_model"),
        custom_instruction=raw.get("custom_instruction"),
        shortcut=raw.get("shortcut"),
        live_insertion=bool(raw.get("live_insertion", False)),
        context_policy=ContextPolicy(
            frozenset(ContextSource(source) for source in raw.get("context_sources", ())),
            bool(raw.get("allow_cloud_context", False)),
        ),
        activation_rules=tuple(ActivationRule(**rule) for rule in raw.get("activation_rules", ())),
        built_in=bool(raw.get("built_in", False)),
    )
