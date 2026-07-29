"""Managed onboarding for the optional gated Cohere Arabic local model."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ProviderError, ProviderErrorKind
from .models import COHERE_LOCAL_ARABIC_MODEL

MINIMUM_MEMORY_BYTES = 8 * 1024**3


@dataclass(frozen=True, slots=True)
class LocalPackStatus:
    installed: bool
    path: Path
    hardware_supported: bool
    message: str


def system_memory_bytes() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError):
        return 0


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


class CohereLocalPackManager:
    """Download gated weights into an OpenWhisper-owned model directory.

    Hugging Face tokens are passed directly to ``snapshot_download`` and never
    written by OpenWhisper. Existing Hugging Face CLI authentication is used
    when no token is entered in the setup dialog.
    """

    marker_name = ".openwhisper-pack"

    def __init__(
        self,
        data_dir: Path,
        *,
        snapshot_download: Callable[..., Any] | None = None,
        token_lookup: Callable[[], str | None] | None = None,
        memory_bytes: Callable[[], int] = system_memory_bytes,
        has_cuda: Callable[[], bool] = cuda_available,
    ) -> None:
        self.path = Path(data_dir) / "models" / "cohere-transcribe-arabic-07-2026"
        self._snapshot_download = snapshot_download
        self._token_lookup = token_lookup
        self._memory_bytes = memory_bytes
        self._has_cuda = has_cuda

    def status(self) -> LocalPackStatus:
        supported = self._has_cuda() or self._memory_bytes() >= MINIMUM_MEMORY_BYTES
        installed = (self.path / self.marker_name).exists()
        if installed and supported:
            message = "Managed local model pack is installed."
        elif installed:
            message = "The model pack is installed, but this hardware check is not supported."
        elif not supported:
            message = "This backend needs a supported GPU or at least 8 GiB of system memory."
        else:
            message = "Hugging Face access and a managed model download are required."
        return LocalPackStatus(installed, self.path, supported, message)

    def install(self, *, token: str | None = None) -> LocalPackStatus:
        status = self.status()
        if not status.hardware_supported:
            raise ProviderError(
                "cohere-local",
                ProviderErrorKind.CONFIGURATION,
                "The local Cohere pack does not meet the hardware check",
            )
        access_token = (token or "").strip() or self._cached_token()
        if not access_token:
            raise ProviderError(
                "cohere-local",
                ProviderErrorKind.AUTHENTICATION,
                "Accept the model terms and sign in to Hugging Face before downloading",
            )
        download = self._snapshot_download or self._load_snapshot_download()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            download(
                repo_id=COHERE_LOCAL_ARABIC_MODEL,
                local_dir=str(self.path),
                token=access_token,
            )
        except Exception as exc:
            raise ProviderError(
                "cohere-local",
                ProviderErrorKind.AUTHENTICATION,
                "The gated model download failed; verify Hugging Face access",
            ) from exc
        # The marker contains only a public model identifier, never a token.
        (self.path / self.marker_name).write_text(
            f"{COHERE_LOCAL_ARABIC_MODEL}\n", encoding="utf-8"
        )
        return self.status()

    def _cached_token(self) -> str | None:
        if self._token_lookup is not None:
            return self._token_lookup()
        try:
            from huggingface_hub import get_token

            return get_token()
        except ImportError:
            return None

    @staticmethod
    def _load_snapshot_download() -> Callable[..., Any]:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ProviderError(
                "cohere-local",
                ProviderErrorKind.CONFIGURATION,
                "Install the cohere-local optional backend before downloading",
            ) from exc
        return snapshot_download
