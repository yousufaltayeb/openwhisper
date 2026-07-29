from __future__ import annotations

import re

import pytest

from openwhisper.core import TranscriptSegment
from openwhisper.core.models import deduplicate_segments


@pytest.mark.parametrize(
    ("name", "first", "second", "must_contain", "expects_arabic"),
    [
        (
            "gulf-arabic",
            "شلونك اليوم خلنا",
            "اليوم خلنا نبدأ",
            ("شلونك", "نبدأ"),
            True,
        ),
        (
            "modern-standard-arabic",
            "مرحبًا بكم في الاجتماع",
            "في الاجتماع الأسبوعي",
            ("مرحبًا", "الأسبوعي"),
            True,
        ),
        (
            "english",
            "Please open the project",
            "the project settings",
            ("Please", "settings"),
            False,
        ),
        (
            "arabic-english-code-switching",
            "افتح OpenWhisper من",
            "OpenWhisper من Settings",
            ("افتح", "OpenWhisper", "Settings"),
            True,
        ),
    ],
)
def test_language_fixtures_survive_boundary_deduplication(
    name, first, second, must_contain, expects_arabic
):
    text = deduplicate_segments(
        (
            TranscriptSegment(0.0, 1.0, first),
            TranscriptSegment(0.8, 2.0, second),
        )
    )

    assert text, name
    assert all(token in text for token in must_contain), name
    assert bool(re.search(r"[\u0600-\u06ff]", text)) is expects_arabic
