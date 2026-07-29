from __future__ import annotations

import wave
from array import array
from pathlib import Path

import pytest

from openwhisper.providers import (
    CohereArabicLocalProvider,
    CohereLocalPackManager,
    ProviderError,
    ProviderErrorKind,
    TranscriptionRequest,
)
from openwhisper.providers.models import COHERE_LOCAL_ARABIC_MODEL


def test_managed_pack_checks_hardware_and_never_persists_token(tmp_path: Path) -> None:
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)

    manager = CohereLocalPackManager(
        tmp_path,
        snapshot_download=download,
        memory_bytes=lambda: 16 * 1024**3,
        has_cuda=lambda: False,
    )
    installed = manager.install(token="hf_private_value")

    assert installed.installed
    assert calls[0]["repo_id"] == COHERE_LOCAL_ARABIC_MODEL
    assert calls[0]["token"] == "hf_private_value"
    assert "hf_private_value" not in (installed.path / manager.marker_name).read_text()


def test_managed_pack_requires_gated_hugging_face_access(tmp_path: Path) -> None:
    manager = CohereLocalPackManager(
        tmp_path,
        token_lookup=lambda: None,
        memory_bytes=lambda: 16 * 1024**3,
        has_cuda=lambda: False,
    )
    with pytest.raises(ProviderError) as raised:
        manager.install()
    assert raised.value.kind is ProviderErrorKind.AUTHENTICATION


def test_managed_pack_rejects_unsupported_hardware_before_download(tmp_path: Path) -> None:
    manager = CohereLocalPackManager(
        tmp_path,
        snapshot_download=lambda **_kwargs: pytest.fail("download should not run"),
        memory_bytes=lambda: 4 * 1024**3,
        has_cuda=lambda: False,
    )
    with pytest.raises(ProviderError) as raised:
        manager.install(token="hf_token")
    assert raised.value.kind is ProviderErrorKind.CONFIGURATION


def test_cohere_local_vad_uses_and_deletes_trimmed_scratch_wav(tmp_path: Path) -> None:
    source = tmp_path / "speech.wav"
    samples = array("h", [0] * 8000 + [1200] * 8000 + [0] * 8000)
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())

    observed = []

    def factory(_model, _device):
        def transcribe(path):
            observed.append(Path(path))
            return {"text": "مرحبا"}

        return transcribe

    provider = CohereArabicLocalProvider(pipeline_factory=factory)
    result = provider.transcribe(TranscriptionRequest(source, language="ar"))

    assert result.text == "مرحبا"
    assert observed[0] != source
    assert not observed[0].exists()
