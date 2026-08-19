from __future__ import annotations

import subprocess

from openwhisper.core.compute import ComputeBackend, ComputeCapability, ComputeProbe


class FakeModel:
    def __init__(self) -> None:
        self.called = False

    def transcribe(self, _path: str, **_kwargs):
        self.called = True
        return iter((object(),)), {"language": "en"}


def test_compute_probe_runs_complete_inference_on_disposable_audio() -> None:
    model = FakeModel()

    result = ComputeProbe(
        model_factory=lambda *_args: model,
    ).probe(ComputeBackend.CPU)

    assert result.available is True
    assert result.validated is True
    assert model.called is True
    assert result.failure_reason is None


def test_automatic_compute_prefers_validated_nvidia_then_amd_then_cpu() -> None:
    capabilities = (
        ComputeCapability("cpu", ComputeBackend.CPU, True, True, ("int8",)),
        ComputeCapability("amd", ComputeBackend.AMD, True, True, ("float16",)),
        ComputeCapability("nvidia", ComputeBackend.NVIDIA, True, True, ("float16",)),
    )
    assert ComputeProbe.choose("auto", capabilities) is ComputeBackend.NVIDIA


def test_accelerator_probe_runs_in_isolated_process_with_selected_extension(
    tmp_path, monkeypatch
) -> None:
    extension = tmp_path / "nvidia"
    (extension / "lib").mkdir(parents=True)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[-2].endswith("probe.wav")
        return subprocess.CompletedProcess(command, 0, '{"supported": ["float16"]}\n', "")

    monkeypatch.setattr("openwhisper.core.compute.subprocess.run", run)
    result = ComputeProbe(
        model=str(tmp_path / "installed-model"),
        environment={"OPENWHISPER_NVIDIA_EXTENSION": str(extension)},
    ).probe(ComputeBackend.NVIDIA)

    assert result.available is True
    assert result.validated is True
    assert result.supported_compute_types == ("float16",)
    assert str(extension / "lib") in calls[0][1]["env"]["LD_LIBRARY_PATH"]
