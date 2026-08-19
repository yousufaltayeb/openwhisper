from __future__ import annotations

from pathlib import Path
from threading import Event

from openwhisper.providers.model_manager import ModelManager, ModelState
from openwhisper.providers.models import FASTER_WHISPER_MODEL_REVISIONS


def test_model_download_is_allowlisted_resumable_and_emits_sanitized_status(tmp_path: Path) -> None:
    finished = Event()
    events: list[tuple[str, dict]] = []

    def download(_repo: str, destination: Path, _cancel, progress) -> None:
        (destination / "weights.bin").write_bytes(b"weights")
        progress(0.5)
        progress(1.0)
        finished.set()

    manager = ModelManager(
        tmp_path,
        downloader=download,
        on_changed=lambda event, payload: events.append((event, dict(payload))),
    )
    manager.download("tiny")
    assert finished.wait(2)
    manager._thread.join(timeout=2)  # the worker is intentionally engine-owned
    status = manager.status("tiny")
    assert status.state is ModelState.INSTALLED
    assert status.installed_size > 0
    assert all("path" not in payload for _event, payload in events)


def test_model_catalog_hides_duplicate_aliases_but_preserves_unknown_selection(
    tmp_path: Path,
) -> None:
    manager = ModelManager(tmp_path, selected_model=lambda: "old-legacy-model")
    ids = [item.id for item in manager.list()]
    assert "turbo" not in ids
    assert "large" not in ids
    legacy = manager.list()[-1]
    assert legacy.id == "old-legacy-model"
    assert legacy.unknown_legacy is True
    assert set(FASTER_WHISPER_MODEL_REVISIONS) == set(ids[:-1])
    assert all(len(revision) == 40 for revision in FASTER_WHISPER_MODEL_REVISIONS.values())
