"""Managed private Qwen3 editing pack served by a loopback ``llama-server``."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Protocol

from ._shared import emit_progress, ensure_not_cancelled, send_json
from .cleanup import _SYSTEM_PROMPT, _openai_compatible_text, cleanup_user_message
from .connection import connection_result
from .contracts import (
    CleanupMode,
    CleanupRequest,
    CleanupResult,
    ConnectionTestResult,
    ProviderProgressStage,
)
from .errors import ProviderError, ProviderErrorKind
from .models import (
    QWEN3_EDITING_GGUF_FILENAME,
    QWEN3_EDITING_GGUF_REPOSITORY,
    QWEN3_EDITING_MODEL,
)
from .transport import HttpRequest, HttpTransport, UrllibTransport, json_body

MINIMUM_QWEN_MEMORY_BYTES = 8 * 1024**3
MAX_EDITING_INPUT_CHARACTERS = 12_000


@dataclass(frozen=True, slots=True)
class LocalEditingPackStatus:
    installed: bool
    path: Path
    hardware_supported: bool
    message: str


class LocalEditingPackManager:
    """Download the Apache-2.0 Qwen3 Q4_K_M file only on user request."""

    marker_name = ".openwhisper-qwen3-pack"

    def __init__(
        self,
        data_dir: Path,
        *,
        download: Callable[..., str] | None = None,
        memory_bytes: Callable[[], int] | None = None,
    ) -> None:
        from .local_pack import system_memory_bytes

        self.path = Path(data_dir) / "models" / "qwen3-4b-q4-k-m"
        self.model_path = self.path / QWEN3_EDITING_GGUF_FILENAME
        self._download = download
        self._memory_bytes = memory_bytes or system_memory_bytes

    def status(self) -> LocalEditingPackStatus:
        supported = self._memory_bytes() >= MINIMUM_QWEN_MEMORY_BYTES
        installed = self.model_path.is_file() and (self.path / self.marker_name).is_file()
        if installed and supported:
            message = "Managed local Qwen3 editing pack is installed (CPU mode)."
        elif installed:
            message = "The local editing pack is installed, but 8 GiB RAM is recommended."
        elif not supported:
            message = "The local editing pack needs at least 8 GiB of system memory."
        else:
            message = "Download the optional Apache-2.0 Qwen3 4B Q4_K_M editing pack."
        return LocalEditingPackStatus(installed, self.path, supported, message)

    def install(self) -> LocalEditingPackStatus:
        status = self.status()
        if not status.hardware_supported:
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.CONFIGURATION,
                "The local Qwen3 editing pack does not meet the memory check",
            )
        download = self._download or self._load_download()
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            downloaded = Path(
                download(
                    repo_id=QWEN3_EDITING_GGUF_REPOSITORY,
                    filename=QWEN3_EDITING_GGUF_FILENAME,
                    local_dir=str(self.path),
                )
            )
        except Exception as exc:
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.UNAVAILABLE,
                "The local Qwen3 model download failed",
            ) from exc
        # hf_hub_download normally puts the requested filename in local_dir;
        # accept a downloader that returns another path only after a safe copy.
        if not self.model_path.exists() and downloaded.is_file():
            downloaded.replace(self.model_path)
        if not self.model_path.is_file():
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.MALFORMED_RESPONSE,
                "The Qwen3 download did not produce the expected GGUF file",
            )
        (self.path / self.marker_name).write_text(
            f"{QWEN3_EDITING_GGUF_REPOSITORY}\n{QWEN3_EDITING_GGUF_FILENAME}\n",
            encoding="utf-8",
        )
        return self.status()

    @staticmethod
    def _load_download() -> Callable[..., str]:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.CONFIGURATION,
                "Install the local editing optional dependencies before downloading",
            ) from exc
        return hf_hub_download


@dataclass(frozen=True, slots=True)
class LlamaServerConfig:
    model_path: Path
    executable: str = "llama-server"
    host: str = "127.0.0.1"
    port: int | None = None
    context_window: int = 4096
    threads: int | None = None
    experimental_gpu: bool = False
    startup_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", Path(self.model_path))
        if self.host != "127.0.0.1":
            raise ValueError("llama-server must bind to the loopback address")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError("port must be in range 1..65535")
        if not 512 <= self.context_window <= 8192:
            raise ValueError("context_window must be between 512 and 8192")
        if self.threads is not None and self.threads < 1:
            raise ValueError("threads must be positive")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup timeout must be positive")


class ProcessLike(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[[list[str], dict[str, str]], ProcessLike]


class LlamaServer:
    """A private, lazily started loopback llama.cpp child process."""

    def __init__(
        self,
        config: LlamaServerConfig,
        *,
        transport: HttpTransport | None = None,
        process_factory: ProcessFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._transport = transport or UrllibTransport()
        self._process_factory = process_factory or _spawn_llama_server
        self._sleep = sleep
        self._process: ProcessLike | None = None
        self._port: int | None = None
        # Keep the loopback server authenticated. The token is never placed in
        # argv, public config, status output, exceptions, or logs.
        self._api_key = token_urlsafe(32)
        self.used_cpu_fallback = False

    @property
    def base_url(self) -> str:
        if self._port is None:
            raise ProviderError(
                "local-qwen3", ProviderErrorKind.UNAVAILABLE, "llama-server is not running"
            )
        return f"http://{self.config.host}:{self._port}"

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if not self.config.model_path.is_file():
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.CONFIGURATION,
                "The Qwen3 GGUF model is not installed",
            )
        self._port = self.config.port or _free_loopback_port()
        try:
            self._start_with_gpu(self.config.experimental_gpu)
        except ProviderError:
            if not self.config.experimental_gpu:
                raise
            self.stop()
            self.used_cpu_fallback = True
            self._start_with_gpu(False)

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                return

    def healthcheck(self) -> None:
        self.start()

    def _start_with_gpu(self, experimental_gpu: bool) -> None:
        assert self._port is not None
        command = [
            self.config.executable,
            "--model",
            str(self.config.model_path),
            "--host",
            self.config.host,
            "--port",
            str(self._port),
            "--ctx-size",
            str(self.config.context_window),
            "--n-gpu-layers",
            "-1" if experimental_gpu else "0",
        ]
        if self.config.threads is not None:
            command.extend(("--threads", str(self.config.threads)))
        try:
            inherited = (
                "PATH",
                "LD_LIBRARY_PATH",
                "HOME",
                "TMPDIR",
                "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR",
                "CUDA_VISIBLE_DEVICES",
                "HIP_VISIBLE_DEVICES",
                "ROCR_VISIBLE_DEVICES",
                "VK_DRIVER_FILES",
            )
            environment = {key: os.environ[key] for key in inherited if key in os.environ}
            environment["LLAMA_API_KEY"] = self._api_key
            self._process = self._process_factory(command, environment)
        except FileNotFoundError as exc:
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.CONFIGURATION,
                "llama-server is not available in this installation",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "local-qwen3", ProviderErrorKind.UNAVAILABLE, "llama-server could not be started"
            ) from exc
        self._wait_for_health()

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                break
            try:
                response = self._transport.send(
                    HttpRequest(
                        "GET",
                        f"{self.base_url}/health",
                        {"Authorization": f"Bearer {self._api_key}"},
                        b"",
                        timeout_seconds=1,
                    )
                )
                if 200 <= response.status < 300:
                    return
            except Exception:
                pass
            self._sleep(0.05)
        raise ProviderError(
            "local-qwen3", ProviderErrorKind.UNAVAILABLE, "llama-server did not become ready"
        )


def _spawn_llama_server(command: list[str], environment: dict[str, str]) -> ProcessLike:
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=environment,
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class Qwen3LocalCleanupProvider:
    """Context-aware local editing via Qwen3 in non-thinking mode."""

    name = "local-qwen3"
    model = QWEN3_EDITING_MODEL

    def __init__(self, server: LlamaServer) -> None:
        self._server = server

    def cleanup(self, request: CleanupRequest) -> CleanupResult:
        ensure_not_cancelled(self.name, request.cancellation)
        if request.mode is CleanupMode.RAW:
            return CleanupResult(request.raw_text, provider=None, model=None)
        if not request.raw_text.strip():
            return CleanupResult("", provider=self.name, model=self.model)
        context_length = (
            sum(len(entry.text) for entry in request.context.entries)
            if request.context is not None
            else 0
        )
        if len(request.raw_text) + context_length > MAX_EDITING_INPUT_CHARACTERS:
            raise ProviderError(
                self.name,
                ProviderErrorKind.UNSUPPORTED_CAPABILITY,
                "The local editing request exceeds its bounded context window",
            )
        emit_progress(self.name, request.progress, ProviderProgressStage.LOADING_MODEL)
        self._server.start()
        ensure_not_cancelled(self.name, request.cancellation)
        emit_progress(self.name, request.progress, ProviderProgressStage.CLEANING)
        payload = send_json(
            self.name,
            self._server._transport,
            HttpRequest(
                method="POST",
                url=f"{self._server.base_url}/v1/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._server._api_key}",
                },
                body=json_body(self._request_body(request)),
                timeout_seconds=60,
                cancellation=request.cancellation,
            ),
            cancellation=request.cancellation,
            progress=request.progress,
        )
        text = _openai_compatible_text(payload)
        if not isinstance(text, str):
            raise ProviderError(
                self.name,
                ProviderErrorKind.MALFORMED_RESPONSE,
                "llama-server response did not contain cleanup text",
            )
        emit_progress(self.name, request.progress, ProviderProgressStage.COMPLETED, fraction=1)
        return CleanupResult(_strip_thinking(text), provider=self.name, model=self.model)

    def test_connection(self) -> ConnectionTestResult:
        return connection_result(self.name, self.model, self._server.healthcheck)

    def close(self) -> None:
        self._server.stop()

    def _request_body(self, request: CleanupRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT
                    + " Do not produce reasoning or <think> blocks; return only edited text.",
                },
                {"role": "user", "content": cleanup_user_message(request)},
            ],
            "temperature": 0,
            "max_tokens": 1024,
            "stream": False,
            # Qwen3's official template recognises this flag. llama.cpp safely
            # ignores it on older builds, while the system prompt is a fallback.
            "chat_template_kwargs": {"enable_thinking": False},
        }


def _strip_thinking(text: str) -> str:
    start = text.find("<think>")
    if start < 0:
        return text.strip()
    end = text.find("</think>", start)
    return (text[end + len("</think>") :] if end >= 0 else "").strip()
