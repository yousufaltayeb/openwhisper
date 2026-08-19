from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openwhisper.core.desktop import (
    CapabilityDesktopIntegration,
    DesktopCapabilities,
    InsertionTarget,
    PortalGlobalShortcutBackend,
)
from openwhisper.core.insertion import DesktopSession, DesktopTextInserter
from openwhisper.core.personalization import ContextPolicy, ContextSource
from openwhisper.core.readiness import ReadinessChecker, ReadinessStatus


class FakePortal:
    def __init__(self) -> None:
        self.requests = []
        self.subscriptions = {}
        self.closed = []
        self.session_watchers = {}
        self.service_watchers = []

    @property
    def available(self) -> bool:
        return True

    def request(self, method, arguments, response) -> None:
        self.requests.append((method, arguments, response))

    def subscribe(self, signal, callback) -> None:
        self.subscriptions[signal] = callback

    def unsubscribe(self, signal, callback) -> None:
        self.subscriptions.pop(signal, None)

    def close_session(self, session_handle) -> None:
        self.closed.append(session_handle)

    def watch_session(self, session_handle, callback) -> None:
        self.session_watchers[session_handle] = callback

    def unwatch_session(self, session_handle, callback) -> None:
        self.session_watchers.pop(session_handle, None)

    def watch_service(self, callback) -> None:
        self.service_watchers.append(callback)

    def unwatch_service(self, callback) -> None:
        if callback in self.service_watchers:
            self.service_watchers.remove(callback)


class Clipboard:
    def copy(self, _text: str) -> None:
        return


class Accessibility:
    def __init__(self, target: InsertionTarget) -> None:
        self.target = target
        self.replaced = []

    def focused_target(self):
        return self.target

    def application_id(self):
        return "org.example.Writer"

    def selected_text(self):
        return "selected private words"

    def surrounding_text(self, *, limit):
        assert limit == 100
        return "surrounding private words"

    def replace_selection(self, text):
        self.replaced.append(text)
        return True


def test_portal_shortcut_waits_for_user_mediated_session_and_binding() -> None:
    portal = FakePortal()
    activations = []
    deactivations = []
    backend = PortalGlobalShortcutBackend(portal)

    backend.register(
        "CTRL+ALT+O",
        lambda: activations.append("on"),
        lambda: deactivations.append("off"),
    )
    assert backend.state == "creating-session"
    assert portal.requests[0][0] == "CreateSession"

    portal.requests[0][2](0, {"session_handle": "/org/freedesktop/portal/session/1"})
    assert backend.state == "binding"
    assert portal.requests[1][0] == "BindShortcuts"
    assert portal.requests[1][1][1][0][0] == "dictation"
    # The portal API requires a parent-window argument before its options map.
    assert portal.requests[1][1][2] == ""
    assert "handle_token" in portal.requests[1][1][3]

    portal.requests[1][2](
        0,
        {"shortcuts": (("dictation", {"trigger_description": "Ctrl+Alt+O"}),)},
    )
    assert backend.state == "listing"
    assert portal.requests[2][0] == "ListShortcuts"
    portal.requests[2][2](
        0,
        {"shortcuts": (("dictation", {"trigger_description": "Ctrl+Alt+O"}),)},
    )
    assert backend.state == "ready"
    assert backend.assigned_shortcuts == {"dictation": "Ctrl+Alt+O"}
    portal.subscriptions["Activated"](("/org/freedesktop/portal/session/1", "dictation", 10, {}))
    portal.subscriptions["Activated"](("/another", "dictation", 11, {}))
    portal.subscriptions["Deactivated"](("/org/freedesktop/portal/session/1", "dictation", 12, {}))
    portal.subscriptions["Deactivated"](("/another", "dictation", 13, {}))
    assert activations == ["on"]
    assert deactivations == ["off"]

    backend.unregister()
    assert portal.closed == ["/org/freedesktop/portal/session/1"]
    assert portal.subscriptions == {}
    assert backend.state == "idle"


def test_portal_binds_and_dispatches_multiple_mode_shortcuts() -> None:
    portal = FakePortal()
    activations = []
    backend = PortalGlobalShortcutBackend(portal)
    backend.register_many(
        {
            "dictation": ("CTRL+ALT+O", lambda: activations.append("raw"), None),
            "mode-email": ("CTRL+ALT+E", lambda: activations.append("email"), None),
        }
    )
    portal.requests[0][2](0, {"session_handle": "/org/freedesktop/portal/session/2"})

    shortcuts = portal.requests[1][1][1]
    assert [shortcut[0] for shortcut in shortcuts] == ["dictation", "mode-email"]
    portal.requests[1][2](
        0,
        {"shortcuts": (("mode-email", {"trigger_description": "Ctrl+Alt+E"}),)},
    )
    portal.requests[2][2](
        0,
        {"shortcuts": (("mode-email", {"trigger_description": "Ctrl+Alt+E"}),)},
    )
    portal.subscriptions["Activated"](("/org/freedesktop/portal/session/2", "mode-email", 20, {}))
    portal.subscriptions["Activated"](("/org/freedesktop/portal/session/2", "unknown", 21, {}))
    assert activations == ["email"]


def test_portal_handles_decline_session_close_and_restart() -> None:
    portal = FakePortal()
    errors = []
    statuses = []
    backend = PortalGlobalShortcutBackend(portal, on_error=errors.append, on_status=statuses.append)
    backend.register("CTRL+ALT+O", lambda: None)
    portal.requests[0][2](0, {"session_handle": "/org/freedesktop/portal/session/3"})
    portal.requests[1][2](1, {})
    assert backend.state == "idle"
    assert statuses[-1]["state"] == "unavailable"

    backend.register("CTRL+ALT+O", lambda: None)
    portal.requests[-1][2](0, {"session_handle": "/org/freedesktop/portal/session/4"})
    portal.session_watchers["/org/freedesktop/portal/session/4"]()
    assert backend.state == "idle"
    assert "closed" in errors[-1]

    backend.register("CTRL+ALT+O", lambda: None)
    portal.service_watchers[-1](False)
    assert backend.state == "idle"
    assert "restarted" in errors[-1]


def test_context_is_opt_in_and_protected_fields_are_never_read() -> None:
    inserter = DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=Clipboard())
    capabilities = DesktopCapabilities(
        session=DesktopSession.X11,
        flatpak=True,
        global_shortcuts_portal=True,
        x11_shortcuts=True,
        atspi=True,
        direct_insertion=True,
        clipboard=True,
        secret_portal=True,
    )
    accessibility = Accessibility(InsertionTarget(editable=True, supports_selection=True))
    integration = CapabilityDesktopIntegration(
        inserter=inserter,
        capabilities=capabilities,
        accessibility=accessibility,
        clipboard_reader=lambda: "clipboard private words",
    )

    assert integration.collect_context() == integration.collect_context(ContextPolicy())
    local_policy = ContextPolicy(
        frozenset(
            {
                ContextSource.APPLICATION,
                ContextSource.SELECTED_TEXT,
                ContextSource.SURROUNDING_TEXT,
            }
        )
    )
    context = integration.collect_context(local_policy, surrounding_limit=100)
    assert context.application_name == "org.example.Writer"
    assert context.selected_text == "selected private words"
    assert context.recent_clipboard is None
    # A local-only policy must never yield content when the caller asks to send
    # it to a cloud provider.
    cloud_context = integration.collect_context(local_policy, cloud=True)
    assert cloud_context.content_for(local_policy, cloud=True) == {}
    assert integration.replace_selection("replacement")
    assert integration.insert("direct text").method.value == "atspi"
    assert accessibility.replaced[-1] == "direct text"

    accessibility.target = InsertionTarget(editable=True, protected=True)
    protected = integration.collect_context(local_policy, surrounding_limit=100)
    assert protected.content_for(local_policy) == {}
    assert not integration.replace_selection("must not insert")


@dataclass
class Audio:
    devices: tuple[object, ...] = (object(),)

    def available_devices(self):
        return self.devices

    def start(self, _config=None):
        return

    def stop(self):
        raise AssertionError("readiness must not record audio")

    def cancel(self):
        return

    def read_pcm(self):
        return None


def test_readiness_checks_are_non_recording_and_provider_tests_are_explicit(tmp_path: Path) -> None:
    capabilities = DesktopCapabilities(
        session=DesktopSession.X11,
        flatpak=True,
        global_shortcuts_portal=True,
        x11_shortcuts=False,
        atspi=True,
        direct_insertion=False,
        clipboard=True,
        secret_portal=False,
    )
    checker = ReadinessChecker(
        audio_capture=Audio(),
        capabilities=capabilities,
        data_dir=tmp_path,
        environment={"FLATPAK_ID": "io.github.yousufaltayeb.OpenWhisper"},
        provider_checks={"openai": lambda: (False, "Authentication failed.")},
        minimum_disk_bytes=1,
        minimum_memory_bytes=1,
    )

    startup = checker.check()
    assert all(not check.id.startswith("provider:") for check in startup.checks)
    assert startup.by_id("microphone").status is ReadinessStatus.READY
    assert startup.by_id("secret-portal").status is ReadinessStatus.ACTION_REQUIRED
    explicit = checker.check(test_providers=True)
    assert explicit.by_id("provider:openai").message == "Authentication failed."
