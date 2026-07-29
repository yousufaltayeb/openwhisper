from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from openwhisper.core.retention import AudioRetentionPolicy, RetainedAudioManager


def test_audio_retention_is_disabled_by_default_and_deletes_capture(tmp_path) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    capture = capture_dir / "openwhisper-recording.wav"
    capture.write_bytes(b"private audio")
    manager = RetainedAudioManager(capture_dir, tmp_path / "retained")

    assert manager.retain(capture) is None
    assert not capture.exists()
    assert not (tmp_path / "retained").exists()


def test_audio_retention_is_private_hashed_and_bounded(tmp_path) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    capture = capture_dir / "openwhisper-recording.wav"
    capture.write_bytes(b"private audio")
    now = datetime.now(UTC)
    manager = RetainedAudioManager(
        capture_dir,
        tmp_path / "retained",
        AudioRetentionPolicy(enabled=True, days=7),
    )

    retained = manager.retain(capture, now=now)

    assert retained is not None
    assert retained.path.read_bytes() == b"private audio"
    assert retained.expires_at == now + timedelta(days=7)
    assert len(retained.sha256) == 64
    assert retained.path.stat().st_mode & 0o777 == 0o600
    assert not capture.exists()
    manager.destroy(retained.path)
    assert not retained.path.exists()


def test_audio_retention_limits_and_path_boundaries(tmp_path) -> None:
    with pytest.raises(ValueError, match="between 1 and 30"):
        AudioRetentionPolicy(enabled=True, days=31)

    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    outside = tmp_path / "openwhisper-outside.wav"
    outside.write_bytes(b"do not touch")
    manager = RetainedAudioManager(
        capture_dir,
        tmp_path / "retained",
        AudioRetentionPolicy(enabled=True),
    )
    with pytest.raises(ValueError, match="outside OpenWhisper temporary storage"):
        manager.retain(outside)
    assert outside.exists()
