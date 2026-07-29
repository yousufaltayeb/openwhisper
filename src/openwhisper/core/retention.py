"""Opt-in captured-audio retention with bounded lifetime and ownership checks."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_AUDIO_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class AudioRetentionPolicy:
    enabled: bool = False
    days: int = 7

    def __post_init__(self) -> None:
        if not 1 <= self.days <= MAX_AUDIO_RETENTION_DAYS:
            raise ValueError("audio retention must be between 1 and 30 days")


@dataclass(frozen=True, slots=True)
class RetainedAudio:
    path: Path
    expires_at: datetime
    sha256: str


class RetainedAudioManager:
    """Move recordings from managed capture storage into private retention.

    The source and destination are both constrained to application-owned
    directories. A failed source ownership check deletes the capture rather
    than allowing sensitive audio to linger.
    """

    def __init__(
        self,
        capture_dir: Path,
        retained_dir: Path,
        policy: AudioRetentionPolicy | None = None,
    ) -> None:
        self.capture_dir = Path(capture_dir).absolute()
        self.retained_dir = Path(retained_dir).absolute()
        self.policy = policy or AudioRetentionPolicy()

    def retain(
        self,
        source: Path,
        *,
        now: datetime | None = None,
    ) -> RetainedAudio | None:
        source = self._capture_path(source)
        if not self.policy.enabled:
            source.unlink(missing_ok=True)
            return None
        self._validate_source(source)
        current = _as_utc(now or datetime.now(UTC))
        self.retained_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.retained_dir, 0o700)
        target = self.retained_dir / f"openwhisper-audio-{uuid.uuid4().hex}.wav"
        digest = hashlib.sha256()
        temporary_path: Path | None = None
        try:
            with (
                source.open("rb") as source_file,
                tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".openwhisper-retain-", dir=self.retained_dir, delete=False
                ) as destination,
            ):
                temporary_path = Path(destination.name)
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, target)
            temporary_path = None
            source.unlink(missing_ok=True)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            source.unlink(missing_ok=True)
            raise
        return RetainedAudio(
            path=target,
            expires_at=current + timedelta(days=self.policy.days),
            sha256=digest.hexdigest(),
        )

    def destroy(self, path: Path) -> None:
        path = Path(path).absolute()
        if path.parent != self.retained_dir or not path.name.startswith("openwhisper-audio-"):
            raise ValueError("audio path is outside OpenWhisper retention storage")
        # Unlinking the directory entry does not follow a substituted symlink.
        path.unlink(missing_ok=True)

    def _capture_path(self, value: Path) -> Path:
        path = Path(value).absolute()
        if path.parent != self.capture_dir or not path.name.startswith("openwhisper-"):
            raise ValueError("capture path is outside OpenWhisper temporary storage")
        return path

    @staticmethod
    def _validate_source(path: Path) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError as error:
            raise ValueError("captured audio no longer exists") from error
        if path.is_symlink() or not path.is_file() or info.st_uid != os.getuid():
            path.unlink(missing_ok=True)
            raise PermissionError("captured audio failed its ownership check")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("audio retention timestamps must be timezone-aware")
    return value.astimezone(UTC)
