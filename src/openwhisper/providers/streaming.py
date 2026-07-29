"""Bounded stable-prefix reconciliation for live local transcription."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WORD = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class ReconciledText:
    """The non-duplicated insertion and the complete stable live text."""

    insertion: str
    stable_text: str


class StablePrefixReconciler:
    """Reconcile overlapping chunk hypotheses without unbounded state.

    Faster-Whisper can repeat a decoded tail when a capture chunk has overlap.
    We compare a bounded, normalization-aware suffix to the next prefix and
    insert only the stable new suffix.  Final batch text is reconciled by token
    alignment rather than counting already-inserted words, so Arabic diacritics
    and punctuation changes do not shift the whole result.
    """

    def __init__(self, *, maximum_overlap_words: int = 48) -> None:
        if not 1 <= maximum_overlap_words <= 256:
            raise ValueError("maximum_overlap_words must be between 1 and 256")
        self.maximum_overlap_words = maximum_overlap_words
        self._stable_text = ""
        self._stable_tokens: list[str] = []

    @property
    def stable_text(self) -> str:
        return self._stable_text

    def reconcile_chunk(self, hypothesis: str) -> ReconciledText:
        """Return just the new stable portion of a chunk hypothesis."""

        incoming = hypothesis.strip()
        if not incoming:
            return ReconciledText("", self._stable_text)
        matches = list(_WORD.finditer(incoming))
        comparable = [_comparable(match.group()) for match in matches]
        overlap = _suffix_prefix_overlap(
            self._stable_tokens[-self.maximum_overlap_words :],
            comparable[: self.maximum_overlap_words],
        )
        if overlap >= len(matches):
            insertion = ""
        else:
            insertion = incoming if overlap == 0 else incoming[matches[overlap].start() :]
        insertion = insertion.strip()
        if insertion:
            self._stable_text = f"{self._stable_text} {insertion}".strip()
            self._stable_tokens.extend(
                _comparable(match.group()) for match in _WORD.finditer(insertion)
            )
            # Keep only comparison state.  The complete display text remains
            # available for the active recording but not provider history.
            if len(self._stable_tokens) > self.maximum_overlap_words:
                self._stable_tokens = self._stable_tokens[-self.maximum_overlap_words :]
        return ReconciledText(insertion, self._stable_text)

    def reconcile_final(self, final_text: str) -> str:
        """Return a final suffix after token alignment with live inserted text.

        No destructive key events are emitted: a live insertion may already be
        inside an arbitrary application.  When the final pass corrects an
        earlier unstable word, the reconciler preserves that live text and
        appends only the aligned continuation, avoiding the brittle historical
        word-count suppression rule.
        """

        final = final_text.strip()
        if not final or not self._stable_text:
            return final
        final_matches = list(_WORD.finditer(final))
        final_tokens = [_comparable(match.group()) for match in final_matches]
        live_tokens = [_comparable(match.group()) for match in _WORD.finditer(self._stable_text)]

        prefix = _common_prefix(live_tokens, final_tokens)
        if prefix == len(final_tokens):
            return ""
        if prefix == len(live_tokens):
            return final[final_matches[prefix].start() :].strip()

        # A correction in the live tail is expected.  Search a bounded suffix
        # for an aligned run in the final transcript, then append after that
        # run.  This is token alignment, not a count-based offset.
        best_final_end = prefix
        live_tail = live_tokens[-self.maximum_overlap_words :]
        for live_start in range(len(live_tail)):
            for final_start in range(prefix, len(final_tokens)):
                length = _matching_run(live_tail[live_start:], final_tokens[final_start:])
                if length and final_start + length > best_final_end:
                    best_final_end = final_start + length
        if best_final_end == 0:
            # There is no safe alignment.  Do not reinsert a full transcript;
            # final batch insertion is intentionally conservative here.
            return ""
        if best_final_end >= len(final_matches):
            return ""
        return final[final_matches[best_final_end].start() :].strip()


def _suffix_prefix_overlap(existing: list[str], incoming: list[str]) -> int:
    for size in range(min(len(existing), len(incoming)), 0, -1):
        if existing[-size:] == incoming[:size]:
            return size
    return 0


def _common_prefix(left: list[str], right: list[str]) -> int:
    size = 0
    for first, second in zip(left, right, strict=False):
        if first != second:
            break
        size += 1
    return size


def _matching_run(left: list[str], right: list[str]) -> int:
    size = 0
    for first, second in zip(left, right, strict=False):
        if first != second:
            break
        size += 1
    return size


def _comparable(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in decomposed if character.isalnum() and character != "ـ"
    )
