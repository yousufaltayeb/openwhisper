"""Engine-owned Faster-Whisper model catalog and resumable cache jobs.

The manager intentionally keeps network/model storage out of the frontend. IPC
can ask for an allowlisted model ID and receives only sanitized status data;
the download worker owns the Hugging Face cache and survives Settings closing.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .models import (
    FASTER_WHISPER_CATALOG,
    FASTER_WHISPER_MODEL_REVISIONS,
    FasterWhisperModel,
    canonical_faster_whisper_model,
    faster_whisper_model,
)


class ModelState(StrEnum):
    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    INSTALLED = "installed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ModelStatus:
    id: str
    state: ModelState
    installed_size: int = 0
    progress: float | None = None
    job_id: str | None = None
    error: str | None = None
    selected: bool = False
    group: str | None = None
    languages: str | None = None
    relative_speed: str | None = None
    relative_quality: str | None = None
    unknown_legacy: bool = False

    def sanitized(self) -> ModelStatus:
        """Return a public-safe copy without paths or exception details."""

        error = None
        if self.error:
            error = self.error if self.error in _PUBLIC_ERRORS else "Model download failed."
        return ModelStatus(
            id=self.id,
            state=self.state,
            installed_size=max(0, int(self.installed_size)),
            progress=(
                None
                if self.progress is None
                else max(0.0, min(1.0, float(self.progress)))
            ),
            job_id=self.job_id,
            error=error,
            selected=self.selected,
            group=self.group,
            languages=self.languages,
            relative_speed=self.relative_speed,
            relative_quality=self.relative_quality,
            unknown_legacy=self.unknown_legacy,
        )


@dataclass(frozen=True, slots=True)
class ModelDownloadJob:
    id: str
    model_id: str
    state: ModelState
    progress: float | None = None
    error: str | None = None

    def sanitized(self) -> ModelDownloadJob:
        error = None
        if self.error:
            error = self.error if self.error in _PUBLIC_ERRORS else "Model download failed."
        return ModelDownloadJob(
            self.id,
            self.model_id,
            self.state,
            None if self.progress is None else max(0.0, min(1.0, self.progress)),
            error,
        )


class ModelDownloadBusyError(RuntimeError):
    """Raised when a second model is requested while one is downloading."""


class ModelManager:
    """Manage one resumable allowlisted model download at a time."""

    def __init__(
        self,
        root: Path,
        *,
        selected_model: Callable[[], str] | None = None,
        on_changed: Callable[[str, Mapping[str, object]], None] | None = None,
        downloader: Callable[..., object] | None = None,
        catalog: Iterable[FasterWhisperModel] = FASTER_WHISPER_CATALOG,
    ) -> None:
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self._catalog = tuple(catalog)
        self._by_id = {item.id: item for item in self._catalog}
        self._selected_model = selected_model or (lambda: "")
        self._on_changed = on_changed
        self._downloader = downloader or self._download_with_huggingface
        self._lock = threading.RLock()
        self._jobs: dict[str, ModelDownloadJob] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None

    @property
    def catalog(self) -> tuple[FasterWhisperModel, ...]:
        return self._catalog

    def list(self, *, selected_model: str | None = None) -> tuple[ModelStatus, ...]:
        selected = canonical_faster_whisper_model(selected_model or self._selected_model())
        with self._lock:
            statuses = [self._status(item, selected=selected == item.id) for item in self._catalog]
            if selected and selected not in self._by_id:
                statuses.append(
                    ModelStatus(
                        id=selected,
                        state=ModelState.NOT_INSTALLED,
                        selected=True,
                        unknown_legacy=True,
                        group="legacy",
                        languages="Unknown",
                        relative_speed="Unknown",
                        relative_quality="Unknown",
                    )
                )
            return tuple(status.sanitized() for status in statuses)

    def status(self, model_id: str, *, selected_model: str | None = None) -> ModelStatus:
        canonical = canonical_faster_whisper_model(model_id)
        model = self._by_id.get(canonical)
        if model is None:
            if canonical != (selected_model or self._selected_model()):
                raise KeyError(model_id)
            return ModelStatus(
                canonical,
                ModelState.NOT_INSTALLED,
                selected=True,
                unknown_legacy=True,
            ).sanitized()
        return self._status(
            model,
            selected=canonical == (selected_model or self._selected_model()),
        ).sanitized()

    def download(self, model_id: str) -> ModelDownloadJob:
        model = self._require_model(model_id)
        with self._lock:
            current = next(
                (job for job in self._jobs.values() if job.state is ModelState.DOWNLOADING),
                None,
            )
            if current is not None:
                if current.model_id == model.id:
                    return current.sanitized()
                raise ModelDownloadBusyError("another model download is already in progress")
            if self._is_installed(model):
                job = ModelDownloadJob(str(uuid.uuid4()), model.id, ModelState.INSTALLED, 1.0)
                self._jobs[job.id] = job
                return job
            job = ModelDownloadJob(str(uuid.uuid4()), model.id, ModelState.DOWNLOADING, 0.0)
            self._jobs[job.id] = job
            cancel = threading.Event()
            self._cancel[job.id] = cancel
            self._emit_job(job)
            self._thread = threading.Thread(
                target=self._run_download,
                args=(job, model, cancel),
                name="openwhisper-model-download",
                daemon=True,
            )
            self._thread.start()
            return job.sanitized()

    def cancel(self, identifier: str) -> ModelDownloadJob:
        with self._lock:
            job = self._find_job(identifier)
            if job.state is not ModelState.DOWNLOADING:
                return job.sanitized()
            self._cancel[job.id].set()
            return job.sanitized()

    def remove(self, model_id: str, *, active_model: str | None = None) -> ModelStatus:
        model = self._require_model(model_id)
        selected = canonical_faster_whisper_model(active_model or self._selected_model())
        if selected == model.id:
            raise ValueError("the active model cannot be removed")
        with self._lock:
            for job in self._jobs.values():
                if job.model_id == model.id and job.state is ModelState.DOWNLOADING:
                    raise ModelDownloadBusyError("cancel the model download before removing it")
            path = self.model_path(model.id)
            if path.exists():
                _safe_remove_tree(path, self.root)
            status = self._status(model, selected=False)
            self._emit_changed(status)
            return status.sanitized()

    def model_path(self, model_id: str) -> Path:
        model = self._require_model(model_id)
        # Keep user-controlled IDs out of paths; the short readable prefix is
        # only diagnostic and the digest makes collisions impractical.
        digest = hashlib.sha256(model.id.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{_safe_prefix(model.id)}-{digest}"

    def _run_download(
        self,
        job: ModelDownloadJob,
        model: FasterWhisperModel,
        cancel: threading.Event,
    ) -> None:
        destination = self.model_path(model.id)
        try:
            destination.mkdir(parents=True, exist_ok=True)

            def progress(value: float | None) -> None:
                if cancel.is_set():
                    raise _DownloadCancelled
                with self._lock:
                    current = self._jobs[job.id]
                    self._jobs[job.id] = ModelDownloadJob(
                        current.id,
                        current.model_id,
                        ModelState.DOWNLOADING,
                        value,
                    )
                    self._emit_job(self._jobs[job.id])

            self._call_downloader(model, destination, cancel, progress)
            if cancel.is_set():
                raise _DownloadCancelled
            (destination / ".openwhisper-complete").write_text("1\n", encoding="ascii")
        except _DownloadCancelled:
            final = ModelDownloadJob(job.id, model.id, ModelState.CANCELLED, None)
        except Exception:
            final = ModelDownloadJob(
                job.id,
                model.id,
                ModelState.ERROR,
                None,
                "Model download failed.",
            )
        else:
            final = ModelDownloadJob(job.id, model.id, ModelState.INSTALLED, 1.0)
        with self._lock:
            self._jobs[job.id] = final
            self._cancel.pop(job.id, None)
            self._emit_job(final)

    def _call_downloader(
        self,
        model: FasterWhisperModel,
        destination: Path,
        cancel: threading.Event,
        progress: Callable[[float | None], None],
    ) -> None:
        try:
            self._downloader(
                model.repo_id,
                FASTER_WHISPER_MODEL_REVISIONS[model.id],
                destination,
                cancel,
                progress,
            )
        except TypeError:
            try:
                self._downloader(model.repo_id, destination, cancel, progress)
            except TypeError:
                # Small injected test/download adapters often expose only
                # ``(repo_id, destination)``; preserving that shape is harmless.
                self._downloader(model.repo_id, destination)

    def _download_with_huggingface(
        self,
        repo_id: str,
        revision: str,
        destination: Path,
        cancel: threading.Event,
        progress: Callable[[float | None], None],
    ) -> None:
        if cancel.is_set():
            raise _DownloadCancelled
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - dependency is core
            raise RuntimeError("model download support is unavailable") from exc
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(destination),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        progress(1.0)

    def _status(self, model: FasterWhisperModel, *, selected: bool) -> ModelStatus:
        job = next(
            (item for item in self._jobs.values() if item.model_id == model.id),
            None,
        )
        state = ModelState.INSTALLED if self._is_installed(model) else ModelState.NOT_INSTALLED
        progress = None
        job_id = None
        error = None
        if job is not None:
            job_id, progress, error = job.id, job.progress, job.error
            if job.state is ModelState.DOWNLOADING:
                state = job.state
            elif job.state is ModelState.ERROR and not self._is_installed(model):
                state = job.state
            elif job.state is ModelState.CANCELLED and not self._is_installed(model):
                state = job.state
        return ModelStatus(
            id=model.id,
            state=state,
            installed_size=(
                _directory_size(self.model_path(model.id))
                if state is ModelState.INSTALLED
                else 0
            ),
            progress=1.0 if state is ModelState.INSTALLED else progress,
            job_id=job_id,
            error=error,
            selected=selected,
            group=model.group.value,
            languages=model.languages,
            relative_speed=model.relative_speed,
            relative_quality=model.relative_quality,
        )

    def _is_installed(self, model: FasterWhisperModel) -> bool:
        path = self.model_path(model.id)
        return path.is_dir() and (path / ".openwhisper-complete").is_file()

    def _require_model(self, model_id: str) -> FasterWhisperModel:
        canonical = canonical_faster_whisper_model(model_id)
        model = faster_whisper_model(canonical)
        if model is None or model.id not in self._by_id:
            raise KeyError(model_id)
        return model

    def _find_job(self, identifier: str) -> ModelDownloadJob:
        job = self._jobs.get(identifier)
        if job is None:
            for candidate in self._jobs.values():
                if candidate.model_id == canonical_faster_whisper_model(identifier):
                    job = candidate
                    break
        if job is None:
            raise KeyError(identifier)
        return job

    def _emit_job(self, job: ModelDownloadJob) -> None:
        status = self._status(self._by_id[job.model_id], selected=False)
        payload = {
            "modelId": status.id,
            "jobId": job.id,
            "state": job.state.value,
            "progress": job.progress,
            "installedSize": status.installed_size,
            "error": job.error,
        }
        if self._on_changed is not None:
            self._on_changed("models.progress", payload)
            if job.state in {ModelState.INSTALLED, ModelState.ERROR, ModelState.CANCELLED}:
                self._on_changed(
                    "models.changed",
                    {"modelId": status.id, "state": status.state.value},
                )

    def _emit_changed(self, status: ModelStatus) -> None:
        if self._on_changed is not None:
            self._on_changed(
                "models.changed",
                {
                    "modelId": status.id,
                    "state": status.state.value,
                    "installedSize": status.installed_size,
                },
            )


class _DownloadCancelled(Exception):
    pass


_PUBLIC_ERRORS = frozenset({"Model download failed.", "Model download was cancelled."})


def _safe_prefix(value: str) -> str:
    prefix = "".join(character if character.isalnum() else "-" for character in value)
    return prefix.strip("-")[:48] or "model"


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _safe_remove_tree(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved.parent != resolved_root or path.is_symlink():
        raise ValueError("model cache path is outside OpenWhisper storage")
    shutil.rmtree(path)
