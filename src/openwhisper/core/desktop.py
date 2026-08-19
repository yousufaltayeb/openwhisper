"""Capability-driven desktop integration boundaries.

The application never assumes that a compositor, portal, or accessibility
service is present.  Each feature is discovered independently so the safe
clipboard path remains available even when direct insertion or global
shortcuts are not.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .insertion import DesktopSession, DesktopTextInserter, InsertionMethod, InsertionResult
from .personalization import ContextPolicy, ContextSource, DictationContext


@dataclass(frozen=True, slots=True)
class InsertionTarget:
    application_id: str | None = None
    role: str | None = None
    editable: bool = False
    protected: bool = False
    supports_selection: bool = False


@dataclass(frozen=True, slots=True)
class DesktopCapabilities:
    session: DesktopSession
    flatpak: bool
    global_shortcuts_portal: bool
    x11_shortcuts: bool
    atspi: bool
    direct_insertion: bool
    clipboard: bool
    secret_portal: bool

    @property
    def can_register_shortcut(self) -> bool:
        return self.global_shortcuts_portal or self.x11_shortcuts


class GlobalShortcutBackend(Protocol):
    """A user-mediated global-shortcut implementation."""

    @property
    def available(self) -> bool: ...

    def register(self, shortcut: str, activate: Callable[[], None]) -> None: ...

    def unregister(self) -> None: ...


PortalResponse = Callable[[int, Mapping[str, object]], None]
PortalSignal = Callable[[tuple[object, ...]], None]


class GlobalShortcutsPortalTransport(Protocol):
    """Minimal transport for the XDG GlobalShortcuts portal request flow."""

    @property
    def available(self) -> bool: ...

    def request(
        self,
        method: str,
        arguments: tuple[object, ...],
        response: PortalResponse,
    ) -> None: ...

    def subscribe(self, signal: str, callback: PortalSignal) -> None: ...

    def unsubscribe(self, signal: str, callback: PortalSignal) -> None: ...

    def close_session(self, session_handle: str) -> None: ...

    def watch_session(self, session_handle: str, callback: Callable[[], None]) -> None: ...

    def unwatch_session(self, session_handle: str, callback: Callable[[], None]) -> None: ...

    def watch_service(self, callback: Callable[[bool], None]) -> None: ...

    def unwatch_service(self, callback: Callable[[bool], None]) -> None: ...


class PortalGlobalShortcutBackend:
    """User-mediated XDG GlobalShortcuts portal backend.

    Registering begins the asynchronous CreateSession → BindShortcuts flow;
    the desktop portal owns approval and key selection.  The backend becomes
    active only after the portal returns a successful BindShortcuts response.
    This keeps the portal state machine independently testable and avoids any
    direct connection to the host session bus from a Flatpak app.
    """

    def __init__(
        self,
        transport: GlobalShortcutsPortalTransport,
        *,
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self._transport = transport
        self._on_error = on_error
        self._on_status = on_status
        self._bindings: dict[str, tuple[str, Callable[[], None], Callable[[], None] | None]] = {}
        self._assigned_shortcuts: dict[str, str] = {}
        self._session_handle: str | None = None
        self._state = "idle"

    @property
    def available(self) -> bool:
        return self._transport.available

    @property
    def state(self) -> str:
        return self._state

    @property
    def assigned_shortcuts(self) -> Mapping[str, str]:
        return dict(self._assigned_shortcuts)

    def register(
        self,
        shortcut: str,
        activate: Callable[[], None],
        deactivate: Callable[[], None] | None = None,
    ) -> None:
        self.register_many({"dictation": (shortcut, activate, deactivate)})

    def register_many(
        self,
        bindings: Mapping[str, tuple[str, Callable[[], None], Callable[[], None] | None]],
    ) -> None:
        """Ask the portal to bind the default and any mode-specific shortcuts."""

        if not bindings:
            raise ValueError("at least one shortcut is required")
        normalized = dict(bindings)
        if any(
            not identifier.strip() or not shortcut.strip()
            for identifier, (shortcut, *_rest) in normalized.items()
        ):
            raise ValueError("shortcut ids and triggers cannot be empty")
        if not self.available:
            raise RuntimeError("the Global Shortcuts portal is unavailable")
        self.unregister()
        self._bindings = normalized
        self._state = "creating-session"
        self._status("requesting-permission")
        watch_service = getattr(self._transport, "watch_service", None)
        if callable(watch_service):
            watch_service(self._service_changed)
        token = f"openwhisper{uuid.uuid4().hex}"
        self._transport.request(
            "CreateSession",
            ({"handle_token": token, "session_handle_token": f"session{uuid.uuid4().hex}"},),
            self._created,
        )

    def unregister(self) -> None:
        if self._state == "ready":
            self._transport.unsubscribe("Activated", self._activated)
            if any(
                deactivate is not None
                for _shortcut, _activate, deactivate in self._bindings.values()
            ):
                self._transport.unsubscribe("Deactivated", self._deactivated)
            try:
                self._transport.unsubscribe("ShortcutsChanged", self._shortcuts_changed)
            except Exception:
                pass
        unwatch_service = getattr(self._transport, "unwatch_service", None)
        if callable(unwatch_service):
            unwatch_service(self._service_changed)
        if self._session_handle:
            unwatch_session = getattr(self._transport, "unwatch_session", None)
            if callable(unwatch_session):
                unwatch_session(self._session_handle, self._session_closed)
            try:
                self._transport.close_session(self._session_handle)
            except Exception:
                pass
        self._bindings.clear()
        self._assigned_shortcuts.clear()
        self._session_handle = None
        self._state = "idle"

    def _created(self, code: int, results: Mapping[str, object]) -> None:
        if self._state != "creating-session":
            return
        if code != 0 or not results.get("session_handle"):
            self._fail("Global shortcut permission was not granted by the desktop portal.")
            return
        self._session_handle = str(results["session_handle"])
        watch_session = getattr(self._transport, "watch_session", None)
        if callable(watch_session):
            watch_session(self._session_handle, self._session_closed)
        self._state = "binding"
        shortcuts = tuple(
            (
                shortcut_id,
                {
                    "description": (
                        "OpenWhisper dictation"
                        if shortcut_id == "dictation"
                        else f"OpenWhisper {shortcut_id.removeprefix('mode-')} mode"
                    ),
                    "preferred_trigger": shortcut,
                },
            )
            for shortcut_id, (shortcut, _activate, _deactivate) in self._bindings.items()
        )
        self._transport.request(
            "BindShortcuts",
            (
                self._session_handle,
                shortcuts,
                "",
                {"handle_token": f"bind{uuid.uuid4().hex}"},
            ),
            self._bound,
        )

    def _bound(self, code: int, results: Mapping[str, object]) -> None:
        if self._state != "binding":
            return
        if code != 0:
            self._fail("Global shortcut binding was declined by the desktop portal.")
            return
        # BindShortcuts may return only a subset. Always ask ListShortcuts for
        # the compositor's authoritative, human-readable trigger descriptions.
        self._assigned_shortcuts = self._parse_shortcuts(results.get("shortcuts"))
        self._state = "listing"
        assert self._session_handle is not None
        self._transport.request(
            "ListShortcuts",
            (
                self._session_handle,
                {"handle_token": f"list{uuid.uuid4().hex}"},
            ),
            self._listed,
        )

    def _listed(self, code: int, results: Mapping[str, object]) -> None:
        if self._state != "listing":
            return
        if code != 0:
            self._fail("The desktop portal could not report the assigned shortcuts.")
            return
        listed = self._parse_shortcuts(results.get("shortcuts"))
        if listed:
            self._assigned_shortcuts = listed
        if not self._assigned_shortcuts:
            self._fail("No global shortcuts were assigned by the desktop portal.")
            return
        # A portal is allowed to bind a strict subset. Never dispatch an action
        # which the compositor omitted from its response.
        self._bindings = {
            identifier: binding
            for identifier, binding in self._bindings.items()
            if identifier in self._assigned_shortcuts
        }
        self._transport.subscribe("Activated", self._activated)
        if any(
            deactivate is not None for _shortcut, _activate, deactivate in self._bindings.values()
        ):
            self._transport.subscribe("Deactivated", self._deactivated)
        try:
            self._transport.subscribe("ShortcutsChanged", self._shortcuts_changed)
        except Exception:
            pass
        self._state = "ready"
        self._status("ready")

    def _activated(self, arguments: tuple[object, ...]) -> None:
        if self._state != "ready":
            return
        # Activated(session_handle, shortcut_id, timestamp, options). Ignore signals from
        # another portal session or another shortcut owned by the application.
        if len(arguments) < 2:
            return
        if str(arguments[0]) != self._session_handle:
            return
        binding = self._bindings.get(str(arguments[1]))
        if binding is None:
            return
        try:
            binding[1]()
        except Exception:
            # Application lifecycle errors must not break the portal signal loop.
            return

    def _deactivated(self, arguments: tuple[object, ...]) -> None:
        if self._state != "ready":
            return
        if len(arguments) < 2:
            return
        if str(arguments[0]) != self._session_handle:
            return
        binding = self._bindings.get(str(arguments[1]))
        if binding is None or binding[2] is None:
            return
        try:
            binding[2]()
        except Exception:
            return

    def _shortcuts_changed(self, arguments: tuple[object, ...]) -> None:
        if self._state != "ready" or len(arguments) < 2:
            return
        if str(arguments[0]) != self._session_handle:
            return
        assigned = self._parse_shortcuts(arguments[1])
        if assigned:
            self._assigned_shortcuts = assigned
            self._status("ready")

    def _session_closed(self) -> None:
        if self._state == "idle":
            return
        # The portal already destroyed the handle; do not issue Close again.
        self._session_handle = None
        self._fail("The desktop portal closed the global shortcut session.")

    def _service_changed(self, available: bool) -> None:
        if available or self._state == "idle":
            return
        self._session_handle = None
        self._fail("The desktop portal restarted; global shortcuts must be registered again.")

    def _fail(self, message: str) -> None:
        self.unregister()
        self._status("unavailable", message=message)
        if self._on_error is not None:
            self._on_error(message)

    def _status(self, state: str, *, message: str | None = None) -> None:
        if self._on_status is None:
            return
        payload: dict[str, object] = {
            "state": state,
            "shortcuts": dict(self._assigned_shortcuts),
        }
        if message:
            payload["message"] = message
        self._on_status(payload)

    def _parse_shortcuts(self, value: object) -> dict[str, str]:
        if not isinstance(value, (list, tuple)):
            return {}
        parsed: dict[str, str] = {}
        for raw_item in value:
            item = raw_item
            arguments = getattr(item, "arguments", None)
            if callable(arguments):
                item = arguments()
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            identifier, raw_properties = item
            identifier = str(identifier)
            if identifier not in self._bindings or not isinstance(raw_properties, Mapping):
                continue
            description = raw_properties.get("trigger_description")
            if not isinstance(description, str) or not description.strip():
                description = self._bindings[identifier][0]
            parsed[identifier] = description
        return parsed


class QtDbusGlobalShortcutsPortalTransport:
    """QtDBus implementation of the portal transport.

    QtDBus is used only to speak to `org.freedesktop.portal.Desktop`; Flatpak
    mediates that service and the manifest intentionally does not allow a host
    session-bus socket.  The small transport can be replaced by a fake in unit
    tests or by dbus-next in a non-Qt shell.
    """

    service = "org.freedesktop.portal.Desktop"
    object_path = "/org/freedesktop/portal/desktop"
    interface = "org.freedesktop.portal.GlobalShortcuts"
    request_interface = "org.freedesktop.portal.Request"
    session_interface = "org.freedesktop.portal.Session"

    def __init__(self) -> None:
        self._connections: dict[tuple[str, int], tuple[str, object, str, str]] = {}
        self._service_watchers: dict[int, tuple[object, object]] = {}

    @property
    def available(self) -> bool:
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            bus = QDBusConnection.sessionBus()
            portal = QDBusInterface(self.service, self.object_path, self.interface, bus)
            return bool(bus.isConnected() and portal.isValid())
        except Exception:
            return False

    def request(
        self,
        method: str,
        arguments: tuple[object, ...],
        response: PortalResponse,
    ) -> None:
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            bus = QDBusConnection.sessionBus()
            portal = QDBusInterface(self.service, self.object_path, self.interface, bus)
            options = arguments[-1] if arguments and isinstance(arguments[-1], dict) else {}
            handle_token = options.get("handle_token") if isinstance(options, dict) else None

            def received(code: int, results: Mapping[str, object]) -> None:
                try:
                    response(int(code), dict(results))
                finally:
                    self._disconnect(request_path, received)

            request_path = self._predicted_request_path(bus, handle_token)
            if request_path is not None:
                # Portal request handles are predictable from the token; listen
                # before the method call so a fast user response cannot race us.
                self._connect(request_path, "Response", received)
            reply = portal.call(method, *arguments)
            actual_path = self._object_path(reply.arguments()[0])
            if request_path is not None and request_path != actual_path:
                self._disconnect(request_path, received)
            request_path = actual_path
            if not request_path:
                raise RuntimeError("portal returned no request handle")
            if (request_path, id(received)) not in self._connections:
                self._connect(request_path, "Response", received)
        except Exception:
            response(2, {})

    def subscribe(self, signal: str, callback: PortalSignal) -> None:
        self._connect(
            self.object_path,
            signal,
            lambda *arguments: callback(tuple(arguments)),
            identity=callback,
        )

    def unsubscribe(self, signal: str, callback: PortalSignal) -> None:
        # PortalGlobalShortcutBackend supplies its own bound callback. Qt's
        # Python binding cannot reliably compare wrapped signal callables, so
        # disconnect every matching bridge created for this callback identity.
        self._disconnect(self.object_path, callback)

    def close_session(self, session_handle: str) -> None:
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            interface = QDBusInterface(
                self.service,
                session_handle,
                self.session_interface,
                QDBusConnection.sessionBus(),
            )
            interface.call("Close")
        except Exception:
            return

    def watch_session(self, session_handle: str, callback: Callable[[], None]) -> None:
        self._connect(
            session_handle,
            "Closed",
            callback,
            identity=callback,
            interface=self.session_interface,
        )

    def unwatch_session(self, session_handle: str, callback: Callable[[], None]) -> None:
        self._disconnect(session_handle, callback)

    def watch_service(self, callback: Callable[[bool], None]) -> None:
        try:
            from PySide6.QtCore import QObject, Slot
            from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher

            class Receiver(QObject):
                @Slot(str, str, str)
                def changed(self, _name: str, _old_owner: str, new_owner: str) -> None:
                    callback(bool(new_owner))

            receiver = Receiver()
            watcher = QDBusServiceWatcher(
                self.service,
                QDBusConnection.sessionBus(),
                QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange,
            )
            watcher.serviceOwnerChanged.connect(receiver.changed)
            self._service_watchers[id(callback)] = (watcher, receiver)
        except Exception:
            return

    def unwatch_service(self, callback: Callable[[bool], None]) -> None:
        self._service_watchers.pop(id(callback), None)

    def _connect(
        self,
        path: str,
        signal: str,
        callback: Callable[..., None],
        *,
        identity: object | None = None,
        interface: str | None = None,
    ) -> None:
        from PySide6.QtCore import SLOT, QObject, Slot
        from PySide6.QtDBus import QDBusConnection

        bus = QDBusConnection.sessionBus()
        interface = interface or (
            self.request_interface if path != self.object_path else self.interface
        )

        if signal == "Response":

            class Receiver(QObject):
                @Slot("uint", "QVariantMap")
                def on_response(self, code: int, results: Mapping[str, object]) -> None:
                    callback(code, results)

            receiver: object = Receiver()
            slot = SLOT("on_response(uint,QVariantMap)")
        elif signal == "Activated":

            class Receiver(QObject):
                @Slot("QDBusObjectPath", str, "qulonglong", "QVariantMap")
                def on_activated(
                    self,
                    session_handle: object,
                    shortcut_id: str,
                    timestamp: int,
                    options: Mapping[str, object],
                ) -> None:
                    callback(session_handle, shortcut_id, timestamp, options)

            receiver = Receiver()
            slot = SLOT("on_activated(QDBusObjectPath,QString,qulonglong,QVariantMap)")
        elif signal == "Deactivated":

            class Receiver(QObject):
                @Slot("QDBusObjectPath", str, "qulonglong", "QVariantMap")
                def on_deactivated(
                    self,
                    session_handle: object,
                    shortcut_id: str,
                    timestamp: int,
                    options: Mapping[str, object],
                ) -> None:
                    callback(session_handle, shortcut_id, timestamp, options)

            receiver = Receiver()
            slot = SLOT("on_deactivated(QDBusObjectPath,QString,qulonglong,QVariantMap)")
        elif signal == "ShortcutsChanged":

            class Receiver(QObject):
                @Slot("QDBusObjectPath", "QVariantList")
                def on_shortcuts_changed(self, session_handle: object, shortcuts: object) -> None:
                    callback(session_handle, shortcuts)

            receiver = Receiver()
            slot = SLOT("on_shortcuts_changed(QDBusObjectPath,QVariantList)")
        elif signal == "Closed":

            class Receiver(QObject):
                @Slot()
                def on_closed(self) -> None:
                    callback()

            receiver = Receiver()
            slot = SLOT("on_closed()")
        else:
            raise ValueError(f"unsupported portal signal: {signal}")
        if not bus.connect(self.service, path, interface, signal, receiver, slot):
            raise RuntimeError(f"could not subscribe to portal {signal}")
        self._connections[(path, id(identity or callback))] = (
            signal,
            receiver,
            slot,
            interface,
        )

    def _disconnect(self, path: str, callback: object) -> None:
        entry = self._connections.pop((path, id(callback)), None)
        if entry is None:
            return
        signal, receiver, slot, interface = entry
        try:
            from PySide6.QtCore import SLOT
            from PySide6.QtDBus import QDBusConnection

            del SLOT
            QDBusConnection.sessionBus().disconnect(
                self.service, path, interface, signal, receiver, slot
            )
        except Exception:
            pass

    @staticmethod
    def _object_path(value: object) -> str:
        path = getattr(value, "path", None)
        if callable(path):
            return str(path())
        return str(value)

    def _predicted_request_path(self, bus: object, handle_token: object) -> str | None:
        if not isinstance(handle_token, str) or not handle_token:
            return None
        try:
            sender = str(bus.baseService()).replace(":", "").replace(".", "_")
        except Exception:
            return None
        return f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"


class X11ShortcutBackend:
    """Thin lifecycle wrapper around the existing X11/pynput shortcut service."""

    def __init__(
        self,
        service_factory: Callable[[str, Callable[[], None]], object],
        *,
        available: bool,
    ) -> None:
        self._service_factory = service_factory
        self._available = available
        self._service: object | None = None

    @property
    def available(self) -> bool:
        return self._available

    def register(self, shortcut: str, activate: Callable[[], None]) -> None:
        self.unregister()
        service = self._service_factory(shortcut, activate)
        getattr(service, "start")()
        self._service = service

    def unregister(self) -> None:
        service, self._service = self._service, None
        if service is not None:
            getattr(service, "stop")()


class AccessibilityBackend(Protocol):
    def focused_target(self) -> InsertionTarget | None: ...

    def application_id(self) -> str | None: ...

    def selected_text(self) -> str | None: ...

    def surrounding_text(self, *, limit: int) -> str | None: ...

    def replace_selection(self, text: str) -> bool: ...


class DesktopIntegration(Protocol):
    """Public boundary for portal, AT-SPI, X11, and clipboard adapters."""

    @property
    def capabilities(self) -> DesktopCapabilities: ...

    def register_shortcut(self, shortcut: str, activate: Callable[[], None]) -> str: ...

    def unregister_shortcut(self) -> None: ...

    def focused_target(self) -> InsertionTarget | None: ...

    def collect_context(
        self,
        policy: ContextPolicy = ContextPolicy(),
        *,
        surrounding_limit: int = 500,
        cloud: bool = False,
    ) -> DictationContext: ...

    def insert(self, text: str, output_mode: str = "insert") -> InsertionResult: ...

    def insert_partial(self, text: str) -> InsertionResult: ...

    def copy(self, text: str) -> None: ...

    def replace_selection(self, text: str) -> bool: ...


def detect_desktop_capabilities(
    environment: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    atspi_available: Callable[[], bool] | None = None,
    portal_global_shortcuts: bool | None = None,
    secret_portal: bool | None = None,
) -> DesktopCapabilities:
    """Discover integrations without opening a microphone or reading content."""

    env = os.environ if environment is None else environment
    session = DesktopSession.from_environment(env)
    flatpak = bool(env.get("FLATPAK_ID"))
    atspi = atspi_available() if atspi_available is not None else _atspi_available()
    # A portal implementation may be injected after probing D-Bus.  Environment
    # capability markers make the fallback deterministic in tests and prevent a
    # claim of portal support merely because a session bus happens to exist.
    portal = (
        portal_global_shortcuts
        if portal_global_shortcuts is not None
        else (
            env.get("OPENWHISPER_GLOBAL_SHORTCUTS_PORTAL") == "1"
            or _global_shortcuts_portal_available()
        )
    )
    secret = (
        secret_portal
        if secret_portal is not None
        else flatpak and env.get("OPENWHISPER_SECRET_PORTAL") == "1"
    )
    direct = (session is DesktopSession.X11 and which("xdotool") is not None) or (
        session is DesktopSession.WAYLAND and which("wtype") is not None
    )
    clipboard = any(which(command) is not None for command in ("wl-copy", "xclip", "xsel"))
    return DesktopCapabilities(
        session=session,
        flatpak=flatpak,
        global_shortcuts_portal=bool(portal),
        x11_shortcuts=session is DesktopSession.X11 and which("xdotool") is not None,
        atspi=atspi,
        direct_insertion=direct,
        clipboard=clipboard,
        secret_portal=bool(secret),
    )


class AtspiAccessibilityBackend:
    """Best-effort AT-SPI text access that always rejects protected fields."""

    def __init__(self, *, desktop_provider: Callable[[], object] | None = None) -> None:
        self._desktop_provider = desktop_provider or _atspi_desktop

    def focused_target(self) -> InsertionTarget | None:
        accessible, pyatspi = self._focused()
        if accessible is None or pyatspi is None:
            return None
        try:
            state = accessible.getState()
            protected = (
                state.contains(pyatspi.STATE_PROTECTED)
                or "password" in str(accessible.getRoleName()).casefold()
            )
            editable = state.contains(pyatspi.STATE_EDITABLE) and not protected
            text = accessible.queryText()
            return InsertionTarget(
                application_id=self.application_id(),
                role=str(accessible.getRoleName()),
                editable=editable,
                protected=protected,
                supports_selection=not protected and text.getNSelections() > 0,
            )
        except Exception:
            return None

    def application_id(self) -> str | None:
        accessible, _pyatspi = self._focused()
        if accessible is None:
            return None
        try:
            application = accessible.getApplication()
            return str(getattr(application, "name", "") or "") or None
        except Exception:
            return None

    def site_identifier(self) -> str | None:
        """Return an exposed document URI for opt-in site activation rules."""

        accessible, _pyatspi = self._nonprotected_text_accessible()
        for _depth in range(8):
            if accessible is None:
                return None
            try:
                attributes = accessible.getAttributes()
            except Exception:
                attributes = ()
            for attribute in attributes:
                key, separator, value = str(attribute).partition(":")
                if separator and key.casefold() in {"url", "uri", "document-uri"}:
                    return value.strip() or None
            try:
                accessible = accessible.parent
            except Exception:
                return None
        return None

    def selected_text(self) -> str | None:
        accessible, _pyatspi = self._nonprotected_text_accessible()
        if accessible is None:
            return None
        try:
            text = accessible.queryText()
            if text.getNSelections() < 1:
                return None
            start, end = text.getSelection(0)
            return str(text.getText(start, end)) or None
        except Exception:
            return None

    def surrounding_text(self, *, limit: int) -> str | None:
        if limit <= 0:
            return None
        accessible, _pyatspi = self._nonprotected_text_accessible()
        if accessible is None:
            return None
        try:
            text = accessible.queryText()
            caret = max(0, int(text.caretOffset))
            start = max(0, caret - limit)
            end = min(int(text.characterCount), caret + limit)
            return str(text.getText(start, end)) or None
        except Exception:
            return None

    def replace_selection(self, text: str) -> bool:
        accessible, pyatspi = self._nonprotected_text_accessible()
        if accessible is None:
            return False
        try:
            if pyatspi is None or not accessible.getState().contains(pyatspi.STATE_EDITABLE):
                return False
            editable = accessible.queryEditableText()
            current = accessible.queryText()
            if current.getNSelections() > 0:
                start, end = current.getSelection(0)
                editable.deleteText(start, end)
                editable.insertText(start, text, len(text))
            else:
                editable.insertText(current.caretOffset, text, len(text))
            return True
        except Exception:
            return False

    def _nonprotected_text_accessible(self) -> tuple[object | None, object | None]:
        accessible, pyatspi = self._focused()
        if accessible is None or pyatspi is None:
            return None, None
        try:
            state = accessible.getState()
            protected = (
                state.contains(pyatspi.STATE_PROTECTED)
                or "password" in str(accessible.getRoleName()).casefold()
            )
            if protected:
                return None, None
        except Exception:
            return None, None
        return accessible, pyatspi

    def _focused(self) -> tuple[object | None, object | None]:
        try:
            import pyatspi

            desktop = self._desktop_provider()
            focused = pyatspi.findDescendant(
                desktop, lambda item: item.getState().contains(pyatspi.STATE_FOCUSED)
            )
            return focused, pyatspi
        except Exception:
            return None, None


class CapabilityDesktopIntegration:
    """Route shortcuts, context, and insertion through separately testable adapters."""

    def __init__(
        self,
        *,
        inserter: DesktopTextInserter,
        capabilities: DesktopCapabilities | None = None,
        accessibility: AccessibilityBackend | None = None,
        portal_shortcuts: GlobalShortcutBackend | None = None,
        x11_shortcuts: GlobalShortcutBackend | None = None,
        clipboard_reader: Callable[[], str | None] | None = None,
    ) -> None:
        self.inserter = inserter
        self.capabilities = capabilities or detect_desktop_capabilities()
        self.accessibility = accessibility
        self.portal_shortcuts = portal_shortcuts
        self.x11_shortcuts = x11_shortcuts
        self.clipboard_reader = clipboard_reader
        self._registered_shortcuts: GlobalShortcutBackend | None = None

    def register_shortcut(self, shortcut: str, activate: Callable[[], None]) -> str:
        """Register through the portal first, then use X11 only when available."""

        self.unregister_shortcut()
        x11 = self.x11_shortcuts if self.capabilities.session is DesktopSession.X11 else None
        for name, backend in (
            ("portal", self.portal_shortcuts),
            ("x11", x11),
        ):
            if backend is None or not backend.available:
                continue
            backend.register(shortcut, activate)
            self._registered_shortcuts = backend
            return name
        raise RuntimeError(
            "No global shortcut backend is available. Use the tray control or enable the "
            "desktop Global Shortcuts portal."
        )

    def unregister_shortcut(self) -> None:
        backend, self._registered_shortcuts = self._registered_shortcuts, None
        if backend is not None:
            backend.unregister()

    def focused_target(self) -> InsertionTarget | None:
        return self.accessibility.focused_target() if self.accessibility is not None else None

    def collect_context(
        self,
        policy: ContextPolicy = ContextPolicy(),
        *,
        surrounding_limit: int = 500,
        cloud: bool = False,
    ) -> DictationContext:
        """Collect only explicitly allowed context and never inspect passwords."""

        if not policy.is_enabled or self.accessibility is None:
            return DictationContext()
        target = self.accessibility.focused_target()
        if target is not None and target.protected:
            return DictationContext()
        application_id = (
            self.accessibility.application_id()
            if policy.permits(ContextSource.APPLICATION, cloud=cloud)
            else None
        )
        selected = (
            self.accessibility.selected_text()
            if policy.permits(ContextSource.SELECTED_TEXT, cloud=cloud)
            else None
        )
        surrounding = (
            self.accessibility.surrounding_text(limit=surrounding_limit)
            if policy.permits(ContextSource.SURROUNDING_TEXT, cloud=cloud)
            else None
        )
        clipboard = (
            self.clipboard_reader()
            if policy.permits(ContextSource.RECENT_CLIPBOARD, cloud=cloud)
            and self.clipboard_reader is not None
            else None
        )
        return DictationContext(
            application_name=application_id,
            selected_text=selected,
            surrounding_text=surrounding,
            recent_clipboard=clipboard,
        )

    def insert(self, text: str, output_mode: str = "insert") -> InsertionResult:
        target = self.focused_target()
        if output_mode == "clipboard":
            return self.inserter.insert(text, output_mode)
        if target is not None and target.protected:
            raise PermissionError("OpenWhisper never inserts into protected/password fields")
        if (
            target is not None
            and target.editable
            and self.accessibility is not None
            and self.accessibility.replace_selection(text)
        ):
            result = InsertionResult(InsertionMethod.ATSPI, inserted=True, copied=False)
            if output_mode == "both":
                try:
                    self.copy(text)
                except Exception:
                    return InsertionResult(
                        InsertionMethod.ATSPI,
                        warning="Transcript inserted, but clipboard copy was unavailable.",
                        inserted=True,
                        copied=False,
                    )
                return InsertionResult(InsertionMethod.ATSPI, inserted=True, copied=True)
            return result
        return self.inserter.insert(text, output_mode)

    def insert_partial(self, text: str) -> InsertionResult:
        target = self.focused_target()
        if target is not None and target.protected:
            raise PermissionError("OpenWhisper never inserts into protected/password fields")
        if (
            target is not None
            and target.editable
            and self.accessibility is not None
            and self.accessibility.replace_selection(text)
        ):
            return InsertionResult(InsertionMethod.ATSPI, inserted=True, copied=False)
        return self.inserter.insert_partial(text)

    def copy(self, text: str) -> None:
        self.inserter.copy(text)

    def replace_selection(self, text: str) -> bool:
        target = self.focused_target()
        if target is None or target.protected or not target.editable:
            return False
        return bool(self.accessibility and self.accessibility.replace_selection(text))


def _atspi_available() -> bool:
    try:
        import pyatspi  # noqa: F401
    except ImportError:
        return False
    return True


def _global_shortcuts_portal_available() -> bool:
    try:
        return QtDbusGlobalShortcutsPortalTransport().available
    except Exception:
        return False


def _atspi_desktop() -> object:
    import pyatspi

    return pyatspi.Registry.getDesktop(0)
