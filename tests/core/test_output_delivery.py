from __future__ import annotations

from dataclasses import dataclass

from openwhisper.core.insertion import DesktopSession, DesktopTextInserter, InsertionMethod


@dataclass
class Clipboard:
    value: str = ""

    def copy(self, text: str) -> None:
        self.value = text


class Backend:
    def __init__(self) -> None:
        self.values: list[str] = []

    def available(self) -> bool:
        return True

    def insert(self, text: str) -> None:
        self.values.append(text)


def test_output_modes_keep_copy_and_insert_independent() -> None:
    clipboard = Clipboard()
    backend = Backend()
    inserter = DesktopTextInserter(
        session=DesktopSession.X11,
        x11=backend,
        clipboard=clipboard,
    )

    copied = inserter.insert("مرحبا hello", "clipboard")
    assert copied.method is InsertionMethod.CLIPBOARD
    assert copied.inserted is False
    assert copied.copied is True
    assert backend.values == []

    both = inserter.insert("مرحبا hello", "both")
    assert both.inserted is True
    assert both.copied is True
    assert backend.values == ["مرحبا hello"]
    assert clipboard.value == "مرحبا hello"
