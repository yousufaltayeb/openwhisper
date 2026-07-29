"""Flatpak-safe cloud credential storage.

The XDG Secret portal exposes one opaque, per-application master secret.  We
expand that secret with SHA-256 and use it only to encrypt an application-owned
credential file in the sandbox data directory.  API keys therefore never enter
SQLite, preferences, diagnostics, or a host keyring.  If the Secret portal is
not available (notably on a minimal dwm session), environment variables and a
process-memory store remain usable but nothing is persisted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from .errors import ProviderError, ProviderErrorKind

KEYRING_SERVICE = "openwhisper"
_CREDENTIALS_VERSION = 1

ENVIRONMENT_VARIABLES: dict[str, str] = {
    "cohere": "COHERE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}


class KeyringBackend(Protocol):
    """Legacy migration-only boundary retained for existing host installs.

    It is never loaded automatically and Flatpak runtime construction does not
    pass one.  The temporary compatibility path lets a user migrate an old
    installation without introducing a host keyring dependency to the package.
    """

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class SecretPortal(Protocol):
    """Adapter for ``org.freedesktop.portal.Secret.RetrieveSecret``.

    A desktop adapter writes the portal-returned secret to a supplied file
    descriptor and returns it here.  ``token`` is opaque portal metadata, never
    credential material, and may be retained with the encrypted envelope.
    """

    def retrieve_secret(self, token: str | None = None) -> tuple[bytes, str | None] | None: ...


class UnavailableSecretPortal:
    """Safe default until a desktop portal client is wired by the shell."""

    def retrieve_secret(self, token: str | None = None) -> tuple[bytes, str | None] | None:
        del token
        return None


class QtSecretPortal:
    """Retrieve the Flatpak per-app master secret over the XDG Secret portal.

    PySide6 already ships with the application, so this does not add a D-Bus
    binding just for credentials.  The implementation is intentionally best
    effort: portal backends are not universally available (notably in some
    wlroots/dwm configurations), in which case the caller gets the documented
    environment/session fallback instead of host-keyring access.
    """

    _service = "org.freedesktop.portal.Desktop"
    _desktop_path = "/org/freedesktop/portal/desktop"
    _secret_interface = "org.freedesktop.portal.Secret"
    _request_interface = "org.freedesktop.portal.Request"

    def retrieve_secret(self, token: str | None = None) -> tuple[bytes, str | None] | None:
        try:
            from PySide6.QtCore import SLOT, QCoreApplication, QEventLoop, QObject, QTimer, Slot
            from PySide6.QtDBus import (
                QDBusConnection,
                QDBusInterface,
                QDBusMessage,
                QDBusUnixFileDescriptor,
            )
        except ImportError:
            return None
        # Credential settings can be used by a headless process.  Never create
        # a GUI/event-loop merely to contact a portal; memory fallback is safer.
        if QCoreApplication.instance() is None or not QDBusUnixFileDescriptor.isSupported():
            return None
        connection = QDBusConnection.sessionBus()
        if not connection.isConnected():
            return None
        handle_token = f"openwhisper{secrets.token_hex(12)}"
        sender = connection.baseService().replace(":", "").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"
        loop = QEventLoop()
        response: dict[str, object] = {}

        class Receiver(QObject):
            @Slot("uint", "QVariantMap")
            def on_response(self, code: int, results: dict[str, object]) -> None:
                response["code"] = code
                response["results"] = results
                loop.quit()

        receiver = Receiver()
        # Subscribe first using the documented predictable request path so a
        # fast portal response cannot race the method reply.
        if not connection.connect(
            self._service,
            request_path,
            self._request_interface,
            "Response",
            receiver,
            SLOT("on_response(uint,QVariantMap)"),
        ):
            return None
        read_socket, write_socket = socket.socketpair()
        try:
            options: dict[str, object] = {"handle_token": handle_token}
            if token:
                options["token"] = token
            interface = QDBusInterface(
                self._service,
                self._desktop_path,
                self._secret_interface,
                connection,
            )
            reply = interface.call(
                "RetrieveSecret", QDBusUnixFileDescriptor(write_socket.fileno()), options
            )
            if reply.type() != QDBusMessage.MessageType.ReplyMessage:
                return None
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            timeout.start(5_000)
            loop.exec()
            timeout.stop()
            if response.get("code") != 0:
                return None
            read_socket.settimeout(1)
            secret = read_socket.recv(4096)
            if not secret:
                return None
            results = response.get("results")
            returned_token = results.get("token") if isinstance(results, dict) else None
            return secret, returned_token if isinstance(returned_token, str) else None
        except Exception:
            return None
        finally:
            read_socket.close()
            write_socket.close()
            connection.disconnect(
                self._service,
                request_path,
                self._request_interface,
                "Response",
                receiver,
                SLOT("on_response(uint,QVariantMap)"),
            )


class PortalCredentialBackend:
    """Authenticated encrypted credential envelope backed by a portal secret."""

    def __init__(self, path: Path, portal: SecretPortal) -> None:
        self.path = Path(path)
        self._portal = portal
        self._portal_token: str | None = None

    @property
    def available(self) -> bool:
        return self._cipher() is not None

    def get(self, provider: str) -> str | None:
        cipher, payload = self._cipher_and_payload()
        if cipher is None or payload is None:
            return None
        encrypted = payload.get("credentials", {}).get(provider)
        if not isinstance(encrypted, str):
            return None
        try:
            return cipher.decrypt(encrypted.encode("ascii")).decode("utf-8").strip() or None
        except Exception:
            # A missing/reinstalled portal secret must not make diagnostics
            # reveal ciphertext or cause one corrupt key to break startup.
            return None

    def set(self, provider: str, api_key: str) -> bool:
        cipher, payload = self._cipher_and_payload(create=True)
        if cipher is None or payload is None:
            return False
        credentials = payload.setdefault("credentials", {})
        if not isinstance(credentials, dict):
            credentials = payload["credentials"] = {}
        credentials[provider] = cipher.encrypt(api_key.encode("utf-8")).decode("ascii")
        self._write(payload)
        return True

    def delete(self, provider: str) -> None:
        cipher, payload = self._cipher_and_payload()
        del cipher
        if payload is None:
            return
        credentials = payload.get("credentials")
        if not isinstance(credentials, dict) or provider not in credentials:
            return
        credentials.pop(provider, None)
        self._write(payload)

    def _cipher_and_payload(self, *, create: bool = False):
        payload = self._read_payload()
        cipher = self._cipher(payload.get("portal_token") if payload else None)
        if cipher is None:
            return None, None
        if payload is None:
            if not create:
                return cipher, None
            payload = {
                "version": _CREDENTIALS_VERSION,
                "portal_token": self._portal_token,
                "credentials": {},
            }
        if payload.get("version") != _CREDENTIALS_VERSION:
            return None, None
        return cipher, payload

    def _cipher(self, token: str | None = None):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return None
        retrieved = self._portal.retrieve_secret(token)
        # ``token`` is optional in the portal API and older backends may not
        # implement it. The per-app secret is stable without that optimization.
        if retrieved is None and token is not None:
            retrieved = self._portal.retrieve_secret()
        if retrieved is None:
            return None
        secret, returned_token = retrieved
        if not isinstance(secret, bytes) or not secret:
            return None
        self._portal_token = returned_token or token
        derived = hashlib.sha256(b"openwhisper credential envelope v1\0" + secret).digest()
        return Fernet(base64.urlsafe_b64encode(derived))

    def _read_payload(self) -> dict[str, object] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, prefix=".credentials-", delete=False
        ) as temporary:
            temporary.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o600)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)
        os.chmod(self.path, 0o600)


def canonical_secret_provider(provider: str) -> str:
    normalized = provider.strip().casefold().replace("_", "-")
    if normalized not in ENVIRONMENT_VARIABLES:
        raise ValueError(f"{provider!r} does not use an API key")
    return normalized


class CredentialStore:
    """Resolve environment overrides, portal-persisted keys, then session keys.

    Environment variables intentionally win, which supports headless testing
    and revocable temporary credentials.  A portal failure is non-fatal: the
    entered key lives only for the current process and users can still use an
    environment variable in a minimal desktop session.
    """

    def __init__(
        self,
        *,
        portal: SecretPortal | None = None,
        storage_path: Path | None = None,
        keyring_backend: KeyringBackend | None = None,
        environment: Mapping[str, str] | None = None,
        service_name: str = KEYRING_SERVICE,
    ) -> None:
        self._environment = environment if environment is not None else os.environ
        self.service_name = service_name
        self._session: dict[str, str] = {}
        self._legacy_keyring = keyring_backend
        location = storage_path or _default_credentials_path()
        self._portal_backend = PortalCredentialBackend(location, portal or QtSecretPortal())

    @property
    def persistent(self) -> bool:
        return self._portal_backend.available

    @property
    def storage_description(self) -> str:
        if self.persistent:
            return "Encrypted with the Secret portal"
        return "Session memory only (Secret portal unavailable)"

    def environment_variable(self, provider: str) -> str:
        return ENVIRONMENT_VARIABLES[canonical_secret_provider(provider)]

    def get(self, provider: str) -> str | None:
        provider = canonical_secret_provider(provider)
        from_environment = self._environment.get(ENVIRONMENT_VARIABLES[provider], "").strip()
        if from_environment:
            return from_environment
        portal_value = self._portal_backend.get(provider)
        if portal_value:
            return portal_value
        session_value = self._session.get(provider)
        if session_value:
            return session_value
        if self._legacy_keyring is not None:
            try:
                stored = self._legacy_keyring.get_password(self.service_name, provider)
            except Exception:
                return None
            return stored.strip() if stored and stored.strip() else None
        return None

    def has(self, provider: str) -> bool:
        return self.get(provider) is not None

    def set(self, provider: str, api_key: str) -> None:
        provider = canonical_secret_provider(provider)
        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError("API key cannot be blank")
        self._session[provider] = cleaned
        if self._portal_backend.set(provider, cleaned):
            return
        if self._legacy_keyring is not None:
            try:
                self._legacy_keyring.set_password(self.service_name, provider, cleaned)
            except Exception:
                # The in-memory copy is still valid for this session.
                return

    def delete(self, provider: str) -> None:
        provider = canonical_secret_provider(provider)
        self._session.pop(provider, None)
        self._portal_backend.delete(provider)
        if self._legacy_keyring is not None:
            try:
                self._legacy_keyring.delete_password(self.service_name, provider)
            except Exception:
                return


def resolve_api_key(
    provider: str,
    *,
    api_key: str | None = None,
    credentials: CredentialStore | None = None,
) -> str:
    """Resolve a non-empty key or raise the portable configuration error."""

    canonical = canonical_secret_provider(provider)
    value = (api_key or "").strip() or (credentials or CredentialStore()).get(canonical)
    if value:
        return value
    raise ProviderError(
        canonical,
        ProviderErrorKind.CONFIGURATION,
        (
            f"{canonical} requires an API key in the Secret portal, session environment, or "
            f"{ENVIRONMENT_VARIABLES[canonical]}"
        ),
    )


def _default_credentials_path() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return root / "openwhisper" / "credentials.json"
