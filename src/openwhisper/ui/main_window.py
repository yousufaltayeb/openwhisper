"""Main OpenWhisper window: dictation, history, providers, and settings."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from openwhisper.core.personalization import (
    BUILTIN_MODES,
    BUILTIN_TRANSFORMS,
    ActivationRule,
    CleanupStyle,
    CommandValidator,
    ConfigurationProposal,
    ContextPolicy,
    ContextSource,
    KeyAction,
    ModeDefinition,
    Snippet,
    TextOutput,
    TransformDefinition,
    TransformEngine,
    TransformKind,
    VocabularyEntry,
)

from .models import AppController, HistoryRow, ProviderOption


class _ControllerBridge(QObject):
    event = Signal(str, object)


class ProviderSetupDialog(QDialog):
    def __init__(self, controller: AppController, provider: ProviderOption, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.close_to_tray = True
        self.provider = provider
        self.setWindowTitle(f"Set up {provider.name}")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        description = QLabel(provider.description)
        description.setWordWrap(True)
        description.setObjectName("Muted")
        layout.addWidget(description)
        self._is_cohere_pack = provider.id == "cohere-local"
        self._is_editing_pack = provider.id == "local-qwen3"
        self._is_local_pack = self._is_cohere_pack or self._is_editing_pack
        if provider.needs_api_key or self._is_cohere_pack:
            form = QFormLayout()
            self.key_input = QLineEdit()
            self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
            if self._is_cohere_pack:
                self.key_input.setPlaceholderText(
                    "Optional if you already signed in with the Hugging Face CLI"
                )
                form.addRow("Hugging Face token", self.key_input)
            else:
                self.key_input.setPlaceholderText(
                    "Stored in Linux Secret Service (or use the provider environment variable)"
                )
                form.addRow("API key", self.key_input)
            layout.addLayout(form)
        else:
            self.key_input = None
            local = QLabel("This provider runs locally and does not require an API key.")
            local.setObjectName("Muted")
            local.setWordWrap(True)
            layout.addWidget(local)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        if self._is_local_pack:
            self.install_button = QPushButton("Install managed pack")
            self.install_button.clicked.connect(self._install_pack)
            actions.addWidget(self.install_button)
        test = QPushButton("Test connection")
        test.clicked.connect(self._test)
        actions.addWidget(test)
        actions.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self._pack_bridge = _ControllerBridge(self)
        self._pack_bridge.event.connect(self._pack_finished)
        if self._is_local_pack:
            status_method = (
                self.controller.local_editing_pack_status
                if self._is_editing_pack
                else self.controller.local_pack_status
            )
            _installed, message = status_method()
            self.status.setText(message)

    def _persist_key(self) -> bool:
        if self._is_local_pack:
            return True
        if self.key_input is None or not self.key_input.text().strip():
            return True
        try:
            self.controller.save_api_key(self.provider.id, self.key_input.text().strip())
            self.key_input.clear()
            return True
        except Exception as exc:
            self.status.setStyleSheet("color:#f38b91")
            self.status.setText(f"Could not store the key: {exc}")
            return False

    def _test(self) -> None:
        if not self._persist_key():
            return
        self.status.setStyleSheet("color:#8f9aae")
        self.status.setText("Testing…")
        QGuiApplication.processEvents()
        try:
            ok, message = self.controller.test_provider(self.provider.id)
        except Exception as exc:
            ok, message = False, str(exc)
        self.status.setStyleSheet("color:#67e8b2" if ok else "color:#f38b91")
        self.status.setText(message)

    def _save(self) -> None:
        if self._persist_key():
            self.accept()

    def _install_pack(self) -> None:
        token = self.key_input.text().strip() if self.key_input is not None else None
        self.install_button.setEnabled(False)
        self.status.setStyleSheet("color:#8f9aae")
        self.status.setText("Checking hardware and downloading the managed pack…")

        def install() -> None:
            try:
                if self._is_editing_pack:
                    ok, message = self.controller.install_local_editing_pack()
                else:
                    ok, message = self.controller.install_local_pack(token or None)
            except Exception as exc:
                ok, message = False, str(exc)
            self._pack_bridge.event.emit("pack", {"ok": ok, "message": message})

        threading.Thread(target=install, name="openwhisper-pack-install", daemon=True).start()

    def _pack_finished(self, _event: str, payload: Mapping[str, Any]) -> None:
        ok = bool(payload.get("ok"))
        self.install_button.setEnabled(True)
        if self.key_input is not None:
            self.key_input.clear()
        self.status.setStyleSheet("color:#67e8b2" if ok else "color:#f38b91")
        self.status.setText(str(payload.get("message", "Pack installation finished.")))


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self._settings = controller.settings()
        self._providers = {item.id: item for item in controller.providers()}
        self._modes: list[ModeDefinition] = list(BUILTIN_MODES)
        self._vocabulary: list[VocabularyEntry] = []
        self._snippets: list[Snippet] = []
        self._transforms: list[TransformDefinition] = list(BUILTIN_TRANSFORMS)
        self._transform_engine = TransformEngine(self._run_transform_provider)
        self._transform_target_external = False
        self._command_validator = CommandValidator()
        self._bridge = _ControllerBridge(self)
        self._bridge.event.connect(self.handle_event)
        controller.subscribe(self._bridge.event.emit)

        self.setWindowTitle("OpenWhisper")
        self.setAccessibleName("OpenWhisper personal dictation")
        self.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
        self.setProperty("reducedMotion", getattr(self._settings, "reduced_motion", False))
        self.setMinimumSize(920, 620)
        self.resize(1040, 700)
        self._build_ui()
        self._load_settings()
        self.refresh_personalization()
        self.refresh_history()
        self.statusBar().showMessage(
            "Ready — audio never leaves your machine with the local provider"
        )
        first_run = getattr(controller, "is_first_run", None)
        if callable(first_run) and first_run():
            QTimer.singleShot(0, self._show_first_run_readiness)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(controller.cancel)

    def _show_first_run_readiness(self) -> None:
        self.select_page(7)
        QMessageBox.information(
            self,
            "Welcome to OpenWhisper",
            "Review microphone, shortcut, insertion, credential, storage, and local-model "
            "readiness below. Context and audio retention start off.",
        )

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(205)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 22, 16, 18)
        brand = QLabel("OpenWhisper")
        brand.setObjectName("Brand")
        side.addWidget(brand)
        tagline = QLabel("Private dictation for Linux")
        tagline.setObjectName("Muted")
        side.addWidget(tagline)
        side.addSpacing(24)
        self.nav_buttons: list[QPushButton] = []
        for index, label in enumerate(
            (
                "Dictate",
                "History",
                "Modes",
                "Vocabulary",
                "Snippets",
                "Transforms",
                "Providers",
                "Diagnostics",
            )
        ):
            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.select_page(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        privacy = QLabel("NO TELEMETRY\nAudio is deleted after processing")
        privacy.setObjectName("Eyebrow")
        privacy.setWordWrap(True)
        side.addWidget(privacy)
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._dictate_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._modes_page())
        self.pages.addWidget(self._vocabulary_page())
        self.pages.addWidget(self._snippets_page())
        self.pages.addWidget(self._transforms_page())
        self.pages.addWidget(self._providers_page())
        self.pages.addWidget(self._diagnostics_page())
        shell.addWidget(self.pages, 1)
        self.select_page(0)

    def _page_header(self, title: str, subtitle: str) -> QVBoxLayout:
        layout = QVBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        detail = QLabel(subtitle)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addSpacing(14)
        return layout

    def _dictate_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.addLayout(
            self._page_header(
                "Dictate",
                "Speak naturally in Arabic, English, or both. Press the shortcut again to stop.",
            )
        )
        mode_row = QHBoxLayout()
        mode_label = QLabel("Mode")
        mode_label.setAccessibleName("Active dictation mode")
        mode_row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.setAccessibleName("Active dictation mode")
        self.mode_combo.setToolTip("Modes keep context disabled until you explicitly enable it.")
        self.mode_combo.currentIndexChanged.connect(self._active_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        self.context_badge = QLabel("Context off")
        self.context_badge.setObjectName("StatusPill")
        self.context_badge.setAccessibleName("Context consent status")
        mode_row.addWidget(self.context_badge)
        layout.addLayout(mode_row)
        layout.addStretch()
        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.record_button = QPushButton("●")
        self.record_button.setObjectName("Record")
        self.record_button.setProperty("recording", False)
        self.record_button.setToolTip("Start dictation")
        self.record_button.clicked.connect(self.controller.toggle_recording)
        center.addWidget(self.record_button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.dictation_status = QLabel("Ready")
        self.dictation_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dictation_status.setStyleSheet("font-size:18px; font-weight:600; margin-top:10px")
        center.addWidget(self.dictation_status)
        self.shortcut_hint = QLabel("")
        self.shortcut_hint.setObjectName("Muted")
        self.shortcut_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.shortcut_hint)
        self.live_preview = QLabel("")
        self.live_preview.setWordWrap(True)
        self.live_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_preview.setMaximumWidth(620)
        self.live_preview.setMinimumHeight(52)
        self.live_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        center.addSpacing(22)
        center.addWidget(self.live_preview, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(center)
        layout.addStretch()

        summary = QFrame()
        summary.setStyleSheet(
            "QFrame { background:#151a23; border:1px solid #29313e; border-radius:10px; }"
        )
        row = QHBoxLayout(summary)
        self.provider_summary = QLabel("")
        self.provider_summary.setObjectName("Muted")
        row.addWidget(self.provider_summary)
        row.addStretch()
        configure = QPushButton("Configure")
        configure.clicked.connect(lambda: self.select_page(6))
        row.addWidget(configure)
        layout.addWidget(summary)
        return page

    def _history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.addLayout(
            self._page_header(
                "History",
                "Raw and cleaned text stay local. Optional retained audio follows its expiry.",
            )
        )
        search_row = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search transcripts…")
        self.history_search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh_history)
        self.history_search.textChanged.connect(lambda: self._search_timer.start())
        search_row.addWidget(self.history_search)
        self.history_mode_filter = QComboBox()
        self.history_mode_filter.addItem("All modes", "")
        self.history_mode_filter.currentIndexChanged.connect(self.refresh_history)
        search_row.addWidget(self.history_mode_filter)
        self.history_provider_filter = QComboBox()
        self.history_provider_filter.addItem("All providers", "")
        self.history_provider_filter.currentIndexChanged.connect(self.refresh_history)
        search_row.addWidget(self.history_provider_filter)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_history)
        search_row.addWidget(refresh)
        layout.addLayout(search_row)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self._copy_history_item)
        self.history_list.currentItemChanged.connect(self._history_selected)
        layout.addWidget(self.history_list, 1)
        self.history_detail = QTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setMaximumHeight(120)
        self.history_detail.setAccessibleName("Selected transcript raw and final text")
        layout.addWidget(self.history_detail)
        actions = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self._copy_history_item(self.history_list.currentItem()))
        actions.addWidget(copy)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete_history_item)
        actions.addWidget(delete)
        retry = QPushButton("Retry")
        retry.clicked.connect(lambda: self._history_action("retry_history"))
        actions.addWidget(retry)
        reclean = QPushButton("Re-clean")
        reclean.clicked.connect(lambda: self._history_action("reclean_history"))
        actions.addWidget(reclean)
        transform = QPushButton("Transform")
        transform.clicked.connect(self._history_transform)
        actions.addWidget(transform)
        actions.addStretch()
        clear = QPushButton("Clear all")
        clear.clicked.connect(self._clear_history)
        actions.addWidget(clear)
        layout.addLayout(actions)
        self.history_statistics = QLabel("")
        self.history_statistics.setObjectName("Muted")
        self.history_statistics.setAccessibleName("Local dictation statistics")
        layout.addWidget(self.history_statistics)
        hint = QLabel("Raw and final text stay local. Context is never shown or stored in history.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        return page

    def _modes_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        left = QVBoxLayout()
        left.addLayout(
            self._page_header(
                "Modes",
                "Every mode owns its cleanup, shortcut, activation, and context consent. "
                "Context starts off for every mode.",
            )
        )
        self.mode_list = QListWidget()
        self.mode_list.setAccessibleName("Dictation modes")
        self.mode_list.currentItemChanged.connect(self._mode_selected)
        left.addWidget(self.mode_list, 1)
        mode_actions = QHBoxLayout()
        new_mode = QPushButton("New custom mode")
        new_mode.clicked.connect(self._new_mode)
        mode_actions.addWidget(new_mode)
        delete_mode = QPushButton("Delete custom mode")
        delete_mode.clicked.connect(self._delete_mode)
        mode_actions.addWidget(delete_mode)
        left.addLayout(mode_actions)
        layout.addLayout(left, 1)

        editor = QGroupBox("Mode consent and routing")
        form = QFormLayout(editor)
        self.mode_name = QLineEdit()
        self.mode_name.setAccessibleName("Mode name")
        form.addRow("Name", self.mode_name)
        self.mode_transcription_provider = QComboBox()
        self.mode_transcription_provider.addItem("Use global setting", None)
        for provider in self._providers.values():
            if provider.supports_transcription and provider.available:
                self.mode_transcription_provider.addItem(provider.name, provider.id)
        form.addRow("Transcription", self.mode_transcription_provider)
        self.mode_transcription_model = QLineEdit()
        self.mode_transcription_model.setPlaceholderText("Use provider default")
        form.addRow("Speech model", self.mode_transcription_model)
        self.mode_language = QComboBox()
        for label, value in (
            ("Automatic", "auto"),
            ("Arabic", "ar"),
            ("Arabic — Saudi Arabia", "ar-SA"),
            ("English", "en"),
        ):
            self.mode_language.addItem(label, value)
        form.addRow("Language", self.mode_language)
        self.mode_cleanup_style = QComboBox()
        for label, value in (
            ("Raw", "raw"),
            ("Clean", "clean"),
            ("Formal / MSA", "formal"),
            ("Message", "message"),
            ("Email", "email"),
            ("Note formatting", "note"),
            ("Smart", "smart"),
            ("Custom", "custom"),
        ):
            self.mode_cleanup_style.addItem(label, value)
        form.addRow("Cleanup style", self.mode_cleanup_style)
        self.mode_cleanup_provider = QComboBox()
        self.mode_cleanup_provider.addItem("Use global setting", None)
        for provider in self._providers.values():
            if provider.supports_cleanup and provider.available:
                self.mode_cleanup_provider.addItem(provider.name, provider.id)
        form.addRow("Cleanup provider", self.mode_cleanup_provider)
        self.mode_cleanup_model = QLineEdit()
        self.mode_cleanup_model.setPlaceholderText("Use provider default")
        form.addRow("Cleanup model", self.mode_cleanup_model)
        self.mode_custom_instruction = QTextEdit()
        self.mode_custom_instruction.setMaximumHeight(70)
        self.mode_custom_instruction.setPlaceholderText(
            "Required for a Custom mode; never add facts or expose unapproved context."
        )
        form.addRow("Instructions", self.mode_custom_instruction)
        self.mode_live = QCheckBox("Insert stable partial text while recording")
        form.addRow("Live behavior", self.mode_live)
        self.mode_shortcut = QLineEdit()
        self.mode_shortcut.setPlaceholderText("Uses the global shortcut when empty")
        form.addRow("Mode shortcut", self.mode_shortcut)
        self.mode_application_rule = QLineEdit()
        self.mode_application_rule.setPlaceholderText("e.g. Thunderbird")
        form.addRow("App activation", self.mode_application_rule)
        self.mode_site_rule = QLineEdit()
        self.mode_site_rule.setPlaceholderText("e.g. docs.example.com")
        form.addRow("Site activation", self.mode_site_rule)
        self.mode_context_checks: dict[ContextSource, QCheckBox] = {}
        context_box = QWidget()
        context_layout = QVBoxLayout(context_box)
        context_layout.setContentsMargins(0, 0, 0, 0)
        for source, label in (
            (ContextSource.APPLICATION, "Active application"),
            (ContextSource.SELECTED_TEXT, "Selected text"),
            (ContextSource.SURROUNDING_TEXT, "Surrounding text"),
            (ContextSource.RECENT_CLIPBOARD, "Recent clipboard"),
        ):
            check = QCheckBox(label)
            check.setAccessibleName(f"Allow {label.lower()} context")
            self.mode_context_checks[source] = check
            context_layout.addWidget(check)
        form.addRow("Use local context", context_box)
        self.mode_cloud_context = QCheckBox("May send the enabled context to a cloud provider")
        self.mode_cloud_context.setAccessibleName("Allow cloud context")
        form.addRow("Cloud consent", self.mode_cloud_context)
        self.mode_context_status = QLabel("Context off")
        self.mode_context_status.setObjectName("StatusPill")
        self.mode_context_status.setWordWrap(True)
        form.addRow("Status", self.mode_context_status)
        save = QPushButton("Save mode")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_mode)
        form.addRow("", save)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_scroll.setWidget(editor)
        layout.addWidget(editor_scroll, 1)
        return page

    def _vocabulary_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        left = QVBoxLayout()
        left.addLayout(
            self._page_header(
                "Vocabulary",
                "Correct names, product terms, and Arabic-English spellings deterministically.",
            )
        )
        self.vocabulary_search = QLineEdit()
        self.vocabulary_search.setPlaceholderText("Search vocabulary…")
        self.vocabulary_search.setClearButtonEnabled(True)
        self.vocabulary_search.textChanged.connect(self.refresh_vocabulary)
        left.addWidget(self.vocabulary_search)
        self.vocabulary_list = QListWidget()
        self.vocabulary_list.setAccessibleName("Vocabulary entries")
        self.vocabulary_list.currentItemChanged.connect(self._vocabulary_selected)
        left.addWidget(self.vocabulary_list, 1)
        actions = QHBoxLayout()
        export_json = QPushButton("Export JSON")
        export_json.clicked.connect(lambda: self._export_vocabulary("json"))
        actions.addWidget(export_json)
        export_csv = QPushButton("Export CSV")
        export_csv.clicked.connect(lambda: self._export_vocabulary("csv"))
        actions.addWidget(export_csv)
        import_json = QPushButton("Import")
        import_json.clicked.connect(self._import_vocabulary)
        actions.addWidget(import_json)
        left.addLayout(actions)
        layout.addLayout(left, 1)

        editor = QGroupBox("Vocabulary entry")
        form = QFormLayout(editor)
        self.vocabulary_written = QLineEdit()
        self.vocabulary_written.setAccessibleName("Vocabulary written form")
        form.addRow("Written form", self.vocabulary_written)
        self.vocabulary_spoken = QLineEdit()
        self.vocabulary_spoken.setPlaceholderText("Aliases separated with |")
        self.vocabulary_spoken.setAccessibleName("Vocabulary spoken forms")
        form.addRow("Recognize", self.vocabulary_spoken)
        self.vocabulary_language = QComboBox()
        self.vocabulary_language.addItems(("auto", "ar", "en"))
        form.addRow("Language", self.vocabulary_language)
        self.vocabulary_case = QCheckBox("Match case exactly")
        form.addRow("", self.vocabulary_case)
        save = QPushButton("Save entry")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_vocabulary)
        form.addRow("", save)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete_vocabulary)
        form.addRow("", delete)
        layout.addWidget(editor, 1)
        return page

    def _snippets_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        left = QVBoxLayout()
        left.addLayout(
            self._page_header(
                "Snippets",
                "Expand deliberate spoken triggers into reusable text. "
                "Snippets are local and static.",
            )
        )
        self.snippet_search = QLineEdit()
        self.snippet_search.setPlaceholderText("Search snippets…")
        self.snippet_search.setClearButtonEnabled(True)
        self.snippet_search.textChanged.connect(self.refresh_snippets)
        left.addWidget(self.snippet_search)
        self.snippet_list = QListWidget()
        self.snippet_list.setAccessibleName("Voice snippets")
        self.snippet_list.currentItemChanged.connect(self._snippet_selected)
        left.addWidget(self.snippet_list, 1)
        snippet_actions = QHBoxLayout()
        export_snippets_json = QPushButton("Export JSON")
        export_snippets_json.clicked.connect(lambda: self._export_snippets("json"))
        snippet_actions.addWidget(export_snippets_json)
        export_snippets_csv = QPushButton("Export CSV")
        export_snippets_csv.clicked.connect(lambda: self._export_snippets("csv"))
        snippet_actions.addWidget(export_snippets_csv)
        import_snippets = QPushButton("Import")
        import_snippets.clicked.connect(self._import_snippets)
        snippet_actions.addWidget(import_snippets)
        left.addLayout(snippet_actions)
        layout.addLayout(left, 1)
        editor = QGroupBox("Voice snippet")
        form = QFormLayout(editor)
        self.snippet_trigger = QLineEdit()
        self.snippet_trigger.setAccessibleName("Snippet voice trigger")
        form.addRow("When I say", self.snippet_trigger)
        self.snippet_expansion = QTextEdit()
        self.snippet_expansion.setMaximumHeight(150)
        self.snippet_expansion.setAccessibleName("Snippet expansion")
        form.addRow("Insert", self.snippet_expansion)
        self.snippet_enabled = QCheckBox("Enabled")
        self.snippet_enabled.setChecked(True)
        form.addRow("", self.snippet_enabled)
        save = QPushButton("Save snippet")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_snippet)
        form.addRow("", save)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete_snippet)
        form.addRow("", delete)
        layout.addWidget(editor, 1)
        return page

    def _transforms_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.addLayout(
            self._page_header(
                "Transforms",
                "Preview changes before applying them. Undo never overwrites text "
                "that changed after apply.",
            )
        )
        controls = QHBoxLayout()
        self.transform_combo = QComboBox()
        self.transform_combo.setAccessibleName("Selected text transform")
        controls.addWidget(self.transform_combo)
        load_selection = QPushButton("Load selection")
        load_selection.clicked.connect(self._load_selected_text)
        controls.addWidget(load_selection)
        preview = QPushButton("Preview")
        preview.clicked.connect(self._preview_transform)
        controls.addWidget(preview)
        self.apply_transform_button = QPushButton("Apply preview")
        self.apply_transform_button.setObjectName("Primary")
        self.apply_transform_button.setEnabled(False)
        self.apply_transform_button.clicked.connect(self._apply_transform)
        controls.addWidget(self.apply_transform_button)
        cancel_preview = QPushButton("Cancel preview")
        cancel_preview.clicked.connect(self._cancel_transform_preview)
        controls.addWidget(cancel_preview)
        undo = QPushButton("Undo")
        undo.clicked.connect(self._undo_transform)
        controls.addWidget(undo)
        layout.addLayout(controls)
        self.transform_text = QTextEdit()
        self.transform_text.setPlaceholderText(
            "Paste or type selected text here to preview a transform…"
        )
        self.transform_text.setAccessibleName("Text selected for transform")
        layout.addWidget(self.transform_text, 1)
        self.transform_diff = QTextEdit()
        self.transform_diff.setReadOnly(True)
        self.transform_diff.setPlaceholderText(
            "A compact before/after diff appears here. Nothing changes until Apply preview."
        )
        self.transform_diff.setAccessibleName("Transform preview diff")
        self.transform_diff.setMaximumHeight(150)
        layout.addWidget(self.transform_diff)
        custom = QHBoxLayout()
        self.custom_transform_name = QLineEdit()
        self.custom_transform_name.setPlaceholderText("Custom transform name")
        self.custom_transform_name.setAccessibleName("Custom transform name")
        custom.addWidget(self.custom_transform_name)
        self.custom_transform_instruction = QLineEdit()
        self.custom_transform_instruction.setPlaceholderText(
            "Instructions for your editing provider"
        )
        self.custom_transform_instruction.setAccessibleName("Custom transform instructions")
        custom.addWidget(self.custom_transform_instruction, 1)
        save_custom = QPushButton("Save custom")
        save_custom.clicked.connect(self._save_custom_transform)
        custom.addWidget(save_custom)
        layout.addLayout(custom)
        command = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText(
            "Command mode: copy last transcript, press enter, switch mode to Clean…"
        )
        self.command_input.setAccessibleName("Command mode")
        self.command_input.returnPressed.connect(self._run_command)
        command.addWidget(self.command_input)
        run_command = QPushButton("Run command")
        run_command.clicked.connect(self._run_command)
        command.addWidget(run_command)
        layout.addLayout(command)
        self.command_result = QLabel("")
        self.command_result.setObjectName("Muted")
        self.command_result.setWordWrap(True)
        layout.addWidget(self.command_result)
        return page

    def _providers_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.addLayout(
            self._page_header(
                "Settings",
                "Choose where transcription and optional cleanup run. "
                "API keys never enter this configuration.",
            )
        )

        transcription = QGroupBox("Transcription")
        form = QFormLayout(transcription)
        self.provider_combo = QComboBox()
        for provider in self._providers.values():
            if not provider.supports_transcription:
                continue
            label = provider.name if provider.available else f"{provider.name} — unavailable"
            self.provider_combo.addItem(label, provider.id)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Provider", self.provider_combo)
        self.model_combo = QComboBox()
        form.addRow("Model", self.model_combo)
        self.device_combo = QComboBox()
        self.device_combo.addItems(("auto", "cpu", "cuda"))
        form.addRow("Compute device", self.device_combo)
        microphone_row = QHBoxLayout()
        self.microphone_combo = QComboBox()
        self.microphone_combo.setAccessibleName("Microphone device")
        microphone_row.addWidget(self.microphone_combo, 1)
        test_microphone = QPushButton("Test")
        test_microphone.clicked.connect(self._test_microphone)
        microphone_row.addWidget(test_microphone)
        form.addRow("Microphone", microphone_row)
        self.microphone_status = QLabel("")
        self.microphone_status.setObjectName("Muted")
        form.addRow("", self.microphone_status)
        self.language_combo = QComboBox()
        for label, code in (
            ("Automatic (Arabic + English)", "auto"),
            ("Arabic", "ar"),
            ("Arabic — Saudi Arabia", "ar-SA"),
            ("English", "en"),
        ):
            self.language_combo.addItem(label, code)
        form.addRow("Language", self.language_combo)
        provider_actions = QHBoxLayout()
        self.provider_state = QLabel("")
        self.provider_state.setObjectName("Muted")
        provider_actions.addWidget(self.provider_state)
        provider_actions.addStretch()
        setup = QPushButton("Provider setup")
        setup.clicked.connect(self._setup_current_provider)
        provider_actions.addWidget(setup)
        form.addRow("", provider_actions)
        layout.addWidget(transcription)

        cleanup = QGroupBox("Transcript cleanup")
        clean_form = QFormLayout(cleanup)
        self.cleanup_mode = QComboBox()
        self.cleanup_mode.addItem("Raw — no changes", "raw")
        self.cleanup_mode.addItem("Clean — light corrections", "clean")
        self.cleanup_mode.addItem("Formal / MSA", "formal")
        self.cleanup_mode.addItem("Custom instructions", "custom")
        self.cleanup_mode.currentIndexChanged.connect(self._cleanup_mode_changed)
        clean_form.addRow("Mode", self.cleanup_mode)
        self.cleanup_provider = QComboBox()
        self.cleanup_provider.addItem("None (use raw fallback)", "none")
        for provider in self._providers.values():
            if provider.supports_cleanup and provider.available:
                self.cleanup_provider.addItem(provider.name, provider.id)
        clean_form.addRow("Cleanup provider", self.cleanup_provider)
        self.custom_prompt = QTextEdit()
        self.custom_prompt.setPlaceholderText(
            "Describe corrections without translating or erasing Arabic dialect and code-switching."
        )
        self.custom_prompt.setMaximumHeight(90)
        clean_form.addRow("Instructions", self.custom_prompt)
        fallback = QLabel("If cleanup fails, OpenWhisper inserts the raw transcript and warns you.")
        fallback.setObjectName("Muted")
        fallback.setWordWrap(True)
        clean_form.addRow("", fallback)
        layout.addWidget(cleanup)

        behavior = QGroupBox("Behavior and privacy")
        behavior_form = QFormLayout(behavior)
        self.shortcut_mode = QComboBox()
        self.shortcut_mode.addItem("Toggle", "toggle")
        self.shortcut_mode.addItem("Push to talk", "push-to-talk")
        behavior_form.addRow("Shortcut mode", self.shortcut_mode)
        self.shortcut_input = QLineEdit()
        self.shortcut_input.setPlaceholderText("<alt>+o")
        behavior_form.addRow("Global shortcut", self.shortcut_input)
        self.live_insertion = QCheckBox("Insert partial text while recording")
        behavior_form.addRow("Live insertion", self.live_insertion)
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSuffix(" days")
        self.retention.setSpecialValueText("Do not retain")
        behavior_form.addRow("Text history", self.retention)
        self.notifications = QCheckBox("Show desktop notifications")
        behavior_form.addRow("Notifications", self.notifications)
        self.reduced_motion = QCheckBox("Reduce interface motion")
        behavior_form.addRow("Accessibility", self.reduced_motion)
        self.retain_audio = QCheckBox("Retain a private recording for re-transcription")
        self.retain_audio.setToolTip(
            "Off by default. Retained audio is deleted on expiry or history deletion."
        )
        self.retain_audio.toggled.connect(self._audio_retention_changed)
        behavior_form.addRow("Audio retention", self.retain_audio)
        self.audio_retention = QSpinBox()
        self.audio_retention.setRange(1, 30)
        self.audio_retention.setValue(7)
        self.audio_retention.setSuffix(" days")
        behavior_form.addRow("Keep audio for", self.audio_retention)
        layout.addWidget(behavior)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_settings)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.addLayout(
            self._page_header(
                "Diagnostics",
                "Readiness checks never display transcript, selected text, API keys, "
                "or captured context.",
            )
        )
        self.diagnostics_status = QLabel("")
        self.diagnostics_status.setObjectName("Muted")
        self.diagnostics_status.setWordWrap(True)
        layout.addWidget(self.diagnostics_status)
        self.diagnostics_list = QListWidget()
        self.diagnostics_list.setAccessibleName("Readiness checks")
        layout.addWidget(self.diagnostics_list, 1)
        refresh = QPushButton("Run readiness checks")
        refresh.clicked.connect(self.refresh_diagnostics)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    def _capability_items(self, method: str, fallback: list[Any]) -> list[Any]:
        callback = getattr(self.controller, method, None)
        if not callable(callback):
            return fallback
        try:
            return list(callback())
        except Exception as exc:
            self.statusBar().showMessage(
                f"Could not load {method.removeprefix('list_')}: {exc}", 5000
            )
            return fallback

    def _capability_save(self, method: str, value: Any) -> bool:
        callback = getattr(self.controller, method, None)
        if not callable(callback):
            return True
        try:
            callback(value)
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return False

    def refresh_personalization(self) -> None:
        self._modes = self._capability_items("list_modes", self._modes)
        self._vocabulary = self._capability_items("list_vocabulary", self._vocabulary)
        self._snippets = self._capability_items("list_snippets", self._snippets)
        self._transforms = self._capability_items("list_transforms", self._transforms)
        self.refresh_modes()
        self.refresh_vocabulary()
        self.refresh_snippets()
        self.refresh_transforms()

    def refresh_modes(self) -> None:
        selected = self.mode_combo.currentData() if hasattr(self, "mode_combo") else None
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_list.blockSignals(True)
        self.mode_list.clear()
        for mode in self._modes:
            self.mode_combo.addItem(mode.name, mode.id)
            item = QListWidgetItem(mode.name)
            item.setData(Qt.ItemDataRole.UserRole, mode)
            item.setToolTip(mode.context_policy.badge())
            self.mode_list.addItem(item)
        self.mode_combo.blockSignals(False)
        self.mode_list.blockSignals(False)
        desired = selected or getattr(self._settings, "active_mode_id", "raw")
        self._find_data(self.mode_combo, desired)
        if self.mode_list.count():
            index = max(0, self.mode_combo.currentIndex())
            self.mode_list.setCurrentRow(index)
        self._active_mode_changed()

    def _active_mode_changed(self) -> None:
        if not hasattr(self, "mode_combo"):
            return
        mode = next(
            (item for item in self._modes if item.id == self.mode_combo.currentData()), None
        )
        if mode is None:
            return
        cloud_context = mode.context_policy.allow_cloud
        self.context_badge.setText(mode.context_policy.badge(cloud=cloud_context))
        self.context_badge.setToolTip(
            "No content is collected until enabled in this mode. "
            + mode.context_policy.badge(cloud=cloud_context)
        )
        callback = getattr(self.controller, "select_mode", None)
        if callable(callback):
            try:
                callback(mode.id)
            except Exception:
                # Selecting a mode is still useful in the shell when a session
                # is in progress; the runtime can report the actionable reason.
                pass

    def _mode_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        mode = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(mode, ModeDefinition):
            return
        self.mode_name.setText(mode.name)
        self._find_data(self.mode_transcription_provider, mode.transcription_provider)
        self.mode_transcription_model.setText(mode.transcription_model or "")
        self._find_data(self.mode_language, mode.language)
        self._find_data(self.mode_cleanup_style, mode.cleanup_style.value)
        self._find_data(self.mode_cleanup_provider, mode.cleanup_provider)
        self.mode_cleanup_model.setText(mode.cleanup_model or "")
        self.mode_custom_instruction.setPlainText(mode.custom_instruction or "")
        self.mode_live.setChecked(mode.live_insertion)
        self.mode_shortcut.setText(mode.shortcut or "")
        rule = mode.activation_rules[0] if mode.activation_rules else None
        self.mode_application_rule.setText(rule.application_pattern if rule else "")
        self.mode_site_rule.setText(rule.site_pattern if rule else "")
        for source, check in self.mode_context_checks.items():
            check.setChecked(source in mode.context_policy.enabled_sources)
        self.mode_cloud_context.setChecked(mode.context_policy.allow_cloud)
        self.mode_context_status.setText(mode.context_policy.badge(cloud=True))

    def _save_mode(self) -> None:
        item = self.mode_list.currentItem()
        mode = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(mode, ModeDefinition):
            return
        sources = frozenset(
            source for source, check in self.mode_context_checks.items() if check.isChecked()
        )
        try:
            policy = ContextPolicy(sources, self.mode_cloud_context.isChecked())
            application = self.mode_application_rule.text().strip()
            site = self.mode_site_rule.text().strip()
            rules = () if not application and not site else (ActivationRule(application, site),)
            updated = replace(
                mode,
                name=self.mode_name.text().strip() or mode.name,
                transcription_provider=self.mode_transcription_provider.currentData(),
                transcription_model=self.mode_transcription_model.text().strip() or None,
                language=str(self.mode_language.currentData()),
                cleanup_style=CleanupStyle(str(self.mode_cleanup_style.currentData())),
                cleanup_provider=self.mode_cleanup_provider.currentData(),
                cleanup_model=self.mode_cleanup_model.text().strip() or None,
                custom_instruction=self.mode_custom_instruction.toPlainText().strip() or None,
                live_insertion=self.mode_live.isChecked(),
                shortcut=self.mode_shortcut.text().strip() or None,
                context_policy=policy,
                activation_rules=rules,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid mode", str(exc))
            return
        if not self._capability_save("save_mode", updated):
            return
        self._modes = [
            updated if candidate.id == updated.id else candidate for candidate in self._modes
        ]
        self.refresh_modes()
        self._find_data(self.mode_combo, updated.id)
        self.statusBar().showMessage("Mode saved — context remains opt-in", 3000)

    def _new_mode(self) -> None:
        mode = ModeDefinition(
            id=f"custom-{uuid.uuid4().hex[:8]}",
            name="Custom mode",
            cleanup_style=CleanupStyle.CUSTOM,
            custom_instruction="Edit conservatively without adding facts.",
        )
        if not self._capability_save("save_mode", mode):
            return
        self._modes.append(mode)
        self.refresh_modes()
        for index in range(self.mode_list.count()):
            item = self.mode_list.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate, ModeDefinition) and candidate.id == mode.id:
                self.mode_list.setCurrentItem(item)
                break

    def _delete_mode(self) -> None:
        item = self.mode_list.currentItem()
        mode = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(mode, ModeDefinition):
            return
        if mode.built_in:
            QMessageBox.information(self, "Built-in mode", "Built-in modes cannot be deleted.")
            return
        callback = getattr(self.controller, "delete_mode", None)
        try:
            if callable(callback):
                callback(mode.id)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete mode", str(exc))
            return
        self._modes = [candidate for candidate in self._modes if candidate.id != mode.id]
        self.refresh_modes()

    def refresh_vocabulary(self) -> None:
        if not hasattr(self, "vocabulary_list"):
            return
        selected = self.vocabulary_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole).id if selected is not None else None
        query = self.vocabulary_search.text().casefold().strip()
        self.vocabulary_list.clear()
        for entry in self._vocabulary:
            haystack = " ".join((entry.written_form, *entry.spoken_forms)).casefold()
            if query and query not in haystack:
                continue
            item = QListWidgetItem(f"{entry.written_form}\n{', '.join(entry.spoken_forms)}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.vocabulary_list.addItem(item)
            if entry.id == selected_id:
                self.vocabulary_list.setCurrentItem(item)

    def _vocabulary_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        entry = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(entry, VocabularyEntry):
            return
        self.vocabulary_written.setText(entry.written_form)
        self.vocabulary_spoken.setText(" | ".join(entry.spoken_forms))
        self.vocabulary_language.setCurrentText(entry.language)
        self.vocabulary_case.setChecked(entry.case_sensitive)

    def _save_vocabulary(self) -> None:
        current = self.vocabulary_list.currentItem()
        existing = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        try:
            entry = VocabularyEntry(
                existing.id if isinstance(existing, VocabularyEntry) else str(uuid.uuid4()),
                self.vocabulary_written.text().strip(),
                tuple(
                    part.strip()
                    for part in self.vocabulary_spoken.text().split("|")
                    if part.strip()
                ),
                self.vocabulary_language.currentText(),
                self.vocabulary_case.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid vocabulary", str(exc))
            return
        if not self._capability_save("save_vocabulary", entry):
            return
        self._vocabulary = [item for item in self._vocabulary if item.id != entry.id] + [entry]
        self.refresh_vocabulary()
        self.statusBar().showMessage("Vocabulary entry saved", 3000)

    def _delete_vocabulary(self) -> None:
        current = self.vocabulary_list.currentItem()
        entry = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(entry, VocabularyEntry):
            return
        callback = getattr(self.controller, "delete_vocabulary", None)
        try:
            if callable(callback):
                callback(entry.id)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete vocabulary", str(exc))
            return
        self._vocabulary = [item for item in self._vocabulary if item.id != entry.id]
        self.vocabulary_written.clear()
        self.vocabulary_spoken.clear()
        self.refresh_vocabulary()

    def _export_vocabulary(self, format: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export vocabulary",
            f"openwhisper-vocabulary.{format}",
            "JSON files (*.json);;CSV files (*.csv)",
        )
        if not path:
            return
        callback = getattr(self.controller, "export_vocabulary", None)
        try:
            if callable(callback):
                payload = callback(format=format)
            else:
                from openwhisper.core.personalization import SQLitePersonalizationStore

                temporary = SQLitePersonalizationStore(Path(":memory:"))
                try:
                    for entry in self._vocabulary:
                        temporary.save_vocabulary(entry)
                    payload = temporary.export_vocabulary(format=format)
                finally:
                    temporary.close()
            Path(path).write_text(payload, encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Could not export vocabulary", str(exc))
            return
        self.statusBar().showMessage("Vocabulary exported", 3000)

    def _import_vocabulary(self) -> None:
        path, selected_filter = QFileDialog.getOpenFileName(
            self, "Import vocabulary", "", "JSON files (*.json);;CSV files (*.csv)"
        )
        if not path:
            return
        format = "csv" if path.casefold().endswith(".csv") or "CSV" in selected_filter else "json"
        try:
            payload = Path(path).read_text(encoding="utf-8")
            callback = getattr(self.controller, "import_vocabulary", None)
            if callable(callback):
                imported = list(callback(payload, format=format))
            else:
                from openwhisper.core.personalization import SQLitePersonalizationStore

                temporary = SQLitePersonalizationStore(Path(":memory:"))
                try:
                    imported = list(temporary.import_vocabulary(payload, format=format))
                finally:
                    temporary.close()
        except Exception as exc:
            QMessageBox.warning(self, "Could not import vocabulary", str(exc))
            return
        by_id = {entry.id: entry for entry in self._vocabulary}
        by_id.update({entry.id: entry for entry in imported})
        self._vocabulary = list(by_id.values())
        self.refresh_vocabulary()
        self.statusBar().showMessage(f"Imported {len(imported)} vocabulary entries", 3000)

    def refresh_snippets(self) -> None:
        if not hasattr(self, "snippet_list"):
            return
        self.snippet_list.clear()
        query = self.snippet_search.text().casefold().strip()
        for snippet in self._snippets:
            if query and query not in f"{snippet.trigger}\n{snippet.expansion}".casefold():
                continue
            prefix = "" if snippet.enabled else "Disabled · "
            item = QListWidgetItem(f"{prefix}{snippet.trigger}\n{snippet.expansion}")
            item.setData(Qt.ItemDataRole.UserRole, snippet)
            self.snippet_list.addItem(item)

    def _snippet_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        snippet = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(snippet, Snippet):
            return
        self.snippet_trigger.setText(snippet.trigger)
        self.snippet_expansion.setPlainText(snippet.expansion)
        self.snippet_enabled.setChecked(snippet.enabled)

    def _save_snippet(self) -> None:
        current = self.snippet_list.currentItem()
        existing = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        try:
            snippet = Snippet(
                existing.id if isinstance(existing, Snippet) else str(uuid.uuid4()),
                self.snippet_trigger.text().strip(),
                self.snippet_expansion.toPlainText(),
                enabled=self.snippet_enabled.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid snippet", str(exc))
            return
        if not self._capability_save("save_snippet", snippet):
            return
        self._snippets = [item for item in self._snippets if item.id != snippet.id] + [snippet]
        self.refresh_snippets()
        self.statusBar().showMessage("Snippet saved", 3000)

    def _delete_snippet(self) -> None:
        current = self.snippet_list.currentItem()
        snippet = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(snippet, Snippet):
            return
        callback = getattr(self.controller, "delete_snippet", None)
        try:
            if callable(callback):
                callback(snippet.id)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete snippet", str(exc))
            return
        self._snippets = [item for item in self._snippets if item.id != snippet.id]
        self.refresh_snippets()

    def _export_snippets(self, format: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export snippets",
            f"openwhisper-snippets.{format}",
            "JSON files (*.json);;CSV files (*.csv)",
        )
        if not path:
            return
        callback = getattr(self.controller, "export_snippets", None)
        try:
            if not callable(callback):
                raise RuntimeError("Snippet export is unavailable")
            Path(path).write_text(callback(format=format), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Could not export snippets", str(exc))

    def _import_snippets(self) -> None:
        path, selected_filter = QFileDialog.getOpenFileName(
            self, "Import snippets", "", "JSON files (*.json);;CSV files (*.csv)"
        )
        if not path:
            return
        format = "csv" if path.casefold().endswith(".csv") or "CSV" in selected_filter else "json"
        callback = getattr(self.controller, "import_snippets", None)
        try:
            if not callable(callback):
                raise RuntimeError("Snippet import is unavailable")
            imported = list(callback(Path(path).read_text(encoding="utf-8"), format=format))
        except Exception as exc:
            QMessageBox.warning(self, "Could not import snippets", str(exc))
            return
        by_id = {snippet.id: snippet for snippet in self._snippets}
        by_id.update({snippet.id: snippet for snippet in imported})
        self._snippets = list(by_id.values())
        self.refresh_snippets()

    def refresh_transforms(self) -> None:
        if not hasattr(self, "transform_combo"):
            return
        selected = self.transform_combo.currentData()
        self.transform_combo.clear()
        for transform in self._transforms:
            if transform.enabled:
                self.transform_combo.addItem(transform.name, transform)
        if selected is not None:
            index = self.transform_combo.findData(selected)
            if index >= 0:
                self.transform_combo.setCurrentIndex(index)

    def _preview_transform(self) -> None:
        transform = self.transform_combo.currentData()
        if not isinstance(transform, TransformDefinition):
            return
        try:
            self._transform_preview = self._transform_engine.preview(
                self.transform_text.toPlainText(), transform
            )
        except ValueError as exc:
            self.transform_diff.setPlainText(str(exc))
            self.apply_transform_button.setEnabled(False)
            return
        self.transform_diff.setPlainText(
            self._transform_preview.diff or "No text change would be made."
        )
        self.apply_transform_button.setEnabled(self._transform_preview.changed)

    def _run_transform_provider(self, text: str, transform: TransformDefinition) -> str:
        callback = getattr(self.controller, "transform_text", None)
        if not callable(callback):
            raise ValueError(f"{transform.name} needs an editing provider")
        return str(callback(text, transform))

    def _load_selected_text(self) -> None:
        callback = getattr(self.controller, "selected_text", None)
        try:
            text = str(callback() if callable(callback) else "")
        except Exception as exc:
            self.transform_diff.setPlainText(str(exc))
            return
        if not text:
            self.transform_diff.setPlainText(
                "No editable selection is exposed by the focused application."
            )
            self._transform_target_external = False
            return
        self.transform_text.setPlainText(text)
        self._transform_target_external = True
        self.transform_diff.setPlainText("Selection loaded. Preview before applying.")

    def _save_custom_transform(self) -> None:
        try:
            transform = TransformDefinition(
                str(uuid.uuid4()),
                self.custom_transform_name.text().strip(),
                TransformKind.CUSTOM,
                self.custom_transform_instruction.text().strip(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid transform", str(exc))
            return
        if not self._capability_save("save_transform", transform):
            return
        self._transforms.append(transform)
        self.refresh_transforms()
        self._find_data(self.transform_combo, transform)
        self.custom_transform_name.clear()
        self.custom_transform_instruction.clear()
        self.statusBar().showMessage("Custom transform saved", 3000)

    def _apply_transform(self) -> None:
        preview = getattr(self, "_transform_preview", None)
        if preview is None:
            return
        proposed = preview.proposed
        if self._transform_target_external:
            callback = getattr(self.controller, "replace_selected_text", None)
            try:
                if not callable(callback) or not callback(proposed):
                    raise RuntimeError("The focused application no longer exposes that selection")
            except Exception as exc:
                self.transform_diff.setPlainText(str(exc))
                return
        self._transform_engine.apply(preview)
        self.transform_text.setPlainText(proposed)
        self.apply_transform_button.setEnabled(False)
        self.statusBar().showMessage("Transform applied; Undo is available", 3000)

    def _cancel_transform_preview(self) -> None:
        self._transform_preview = None
        self.transform_diff.clear()
        self.apply_transform_button.setEnabled(False)
        self.statusBar().showMessage("Transform preview cancelled", 2500)

    def _undo_transform(self) -> None:
        current = self.transform_text.toPlainText()
        undone = self._transform_engine.undo(current)
        if undone == current:
            self.statusBar().showMessage("Nothing safe to undo", 2500)
            return
        self.transform_text.setPlainText(undone)
        self.statusBar().showMessage("Transform undone", 2500)

    def _run_command(self) -> None:
        command = self.command_input.text().strip()
        if not command:
            return
        try:
            outcome = self._command_validator.validate(
                command, selected_text=self.transform_text.textCursor().selectedText()
            )
        except ValueError as exc:
            self.command_result.setText(str(exc))
            return
        if isinstance(outcome, ConfigurationProposal):
            accepted = (
                QMessageBox.question(
                    self,
                    "Confirm setting change",
                    outcome.summary,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            )
            if accepted:
                callback = getattr(self.controller, "apply_configuration_proposal", None)
                try:
                    if callable(callback):
                        callback(outcome.key, outcome.value)
                    self.command_result.setText("Setting proposal confirmed")
                except Exception as exc:
                    self.command_result.setText(str(exc))
            else:
                self.command_result.setText("Setting proposal cancelled")
        elif isinstance(outcome, KeyAction):
            callback = getattr(self.controller, "run_key_action", None)
            if callable(callback):
                callback(outcome.key)
            self.command_result.setText(f"Key action: {outcome.key}")
        elif isinstance(outcome, TextOutput):
            method = {
                "__copy_last_transcript__": "copy_last_transcript",
                "__paste_last_transcript__": "paste_last_transcript",
            }.get(outcome.text)
            callback = getattr(self.controller, method, None) if method else None
            try:
                if callable(callback):
                    callback()
                    self.command_result.setText("Command completed")
                else:
                    selected = self.transform_text.textCursor().selectedText()
                    run = getattr(self.controller, "run_text_command", None)
                    if not callable(run):
                        raise RuntimeError("Generation and rewriting require an editing provider.")
                    generated = str(run(outcome.text, selected_text=selected))
                    if selected:
                        cursor = self.transform_text.textCursor()
                        cursor.insertText(generated)
                        if self._transform_target_external:
                            replace_selection = getattr(
                                self.controller, "replace_selected_text", None
                            )
                            if callable(replace_selection):
                                replace_selection(generated)
                    else:
                        insert = getattr(self.controller, "insert_text", None)
                        if callable(insert):
                            insert(generated)
                    self.command_result.setText("Command completed")
            except Exception as exc:
                self.command_result.setText(str(exc))
        else:
            self.history_search.setText(outcome.query)
            self.select_page(1)
            self.command_result.setText(f"Searching local history for “{outcome.query}”")

    def refresh_diagnostics(self) -> None:
        callback = getattr(self.controller, "readiness_checks", None)
        try:
            checks = (
                callback()
                if callable(callback)
                else {
                    "Microphone": "Available when a capture backend is selected",
                    "Shortcut": "Configured",
                    "Insertion": "Capability checked at dictation time",
                    "Secret portal": "Credentials are never stored in settings",
                    "Context": "Disabled by default in every mode",
                }
            )
        except Exception as exc:
            self.diagnostics_status.setText(f"Checks could not run: {exc}")
            return
        self.diagnostics_list.clear()
        for name, result in dict(checks).items():
            self.diagnostics_list.addItem(f"{name}: {result}")
        self.diagnostics_status.setText("No transcript or context content is shown here.")

    def select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            self.refresh_history()
        elif 2 <= index <= 5:
            self.refresh_personalization()
        elif index == 7:
            self.refresh_diagnostics()

    def _find_data(self, combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_settings(self) -> None:
        settings = self._settings
        self._find_data(self.provider_combo, settings.transcription_provider)
        self._provider_changed()
        self._find_data(self.model_combo, settings.transcription_model)
        self.device_combo.setCurrentText(settings.device)
        self._load_audio_devices(getattr(settings, "audio_device_id", None))
        self._find_data(self.language_combo, settings.language)
        self._find_data(self.cleanup_mode, settings.cleanup_mode)
        self._find_data(self.cleanup_provider, settings.cleanup_provider)
        self.custom_prompt.setPlainText(settings.custom_cleanup_prompt)
        self._find_data(self.shortcut_mode, settings.shortcut_mode)
        self.shortcut_input.setText(settings.shortcut)
        self.live_insertion.setChecked(settings.live_insertion)
        self.retention.setValue(settings.retention_days)
        self.notifications.setChecked(settings.notifications)
        self.reduced_motion.setChecked(getattr(settings, "reduced_motion", False))
        self.retain_audio.setChecked(getattr(settings, "retain_audio", False))
        self.audio_retention.setValue(getattr(settings, "audio_retention_days", 7))
        self._audio_retention_changed(self.retain_audio.isChecked())
        self._cleanup_mode_changed()
        self.shortcut_hint.setText(f"Shortcut: {settings.shortcut}")
        provider = self._providers.get(settings.transcription_provider)
        self.provider_summary.setText(
            f"{provider.name if provider else settings.transcription_provider}  ·  "
            f"{settings.transcription_model}  ·  {settings.device}"
        )

    def _provider_changed(self) -> None:
        provider = self._providers.get(self.provider_combo.currentData())
        previous_model = self.model_combo.currentText()
        self.model_combo.clear()
        if provider is None:
            return
        self.model_combo.addItems(provider.models)
        if previous_model in provider.models:
            self.model_combo.setCurrentText(previous_model)
        has_key = self.controller.has_api_key(provider.id) if provider.needs_api_key else True
        if not provider.available:
            state = provider.unavailable_reason or "Unavailable"
        elif provider.needs_api_key and not has_key:
            state = "API key required"
        elif provider.supports_streaming:
            state = "Ready · batch and streaming"
        else:
            state = "Ready · batch transcription"
        self.provider_state.setText(state)
        live_available = provider.supports_streaming and self.cleanup_mode.currentData() == "raw"
        self.live_insertion.setEnabled(live_available)
        self.live_insertion.setToolTip(
            ""
            if live_available
            else "Live insertion requires Faster Whisper with Raw cleanup in v0.1."
        )
        if not live_available:
            self.live_insertion.setChecked(False)

    def _cleanup_mode_changed(self) -> None:
        self.custom_prompt.setEnabled(self.cleanup_mode.currentData() == "custom")
        self._provider_changed()

    def _setup_current_provider(self) -> None:
        provider = self._providers.get(self.provider_combo.currentData())
        if provider is None:
            return
        dialog = ProviderSetupDialog(self.controller, provider, self)
        dialog.exec()
        self._provider_changed()

    def _load_audio_devices(self, selected_id: str | None) -> None:
        self.microphone_combo.clear()
        self.microphone_combo.addItem("Desktop default", None)
        callback = getattr(self.controller, "audio_devices", None)
        try:
            devices = callback() if callable(callback) else ()
        except Exception as exc:
            self.microphone_status.setText(str(exc))
            return
        for device in devices:
            label = device.description + (" — default" if device.is_default else "")
            self.microphone_combo.addItem(label, device.id)
        self._find_data(self.microphone_combo, selected_id)
        self.microphone_status.setText(f"{len(devices)} microphone(s) available")

    def _test_microphone(self) -> None:
        callback = getattr(self.controller, "test_microphone", None)
        if not callable(callback):
            return
        ok, message = callback(self.microphone_combo.currentData())
        self.microphone_status.setText(("Ready — " if ok else "Action needed — ") + message)

    def _save_settings(self) -> None:
        provider = self._providers.get(self.provider_combo.currentData())
        if provider is None:
            return
        settings = replace(
            self._settings,
            transcription_provider=provider.id,
            transcription_model=self.model_combo.currentText(),
            device=self.device_combo.currentText(),
            audio_device_id=self.microphone_combo.currentData(),
            language=self.language_combo.currentData(),
            cleanup_mode=self.cleanup_mode.currentData(),
            cleanup_provider=self.cleanup_provider.currentData(),
            custom_cleanup_prompt=self.custom_prompt.toPlainText().strip(),
            shortcut_mode=self.shortcut_mode.currentData(),
            shortcut=self.shortcut_input.text().strip() or "<alt>+o",
            live_insertion=self.live_insertion.isChecked() and provider.supports_streaming,
            retention_days=self.retention.value(),
            notifications=self.notifications.isChecked(),
            reduced_motion=self.reduced_motion.isChecked(),
            retain_audio=self.retain_audio.isChecked(),
            audio_retention_days=self.audio_retention.value(),
        )
        try:
            self.controller.save_settings(settings)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return
        self._settings = settings
        self.setProperty("reducedMotion", settings.reduced_motion)
        self.style().unpolish(self)
        self.style().polish(self)
        self._load_settings()
        self.statusBar().showMessage("Settings saved", 3000)

    def _audio_retention_changed(self, enabled: bool) -> None:
        self.audio_retention.setEnabled(enabled)
        self.audio_retention.setToolTip(
            "Audio retention is disabled"
            if not enabled
            else "Audio is deleted after this many days"
        )

    def refresh_history(self) -> None:
        query = self.history_search.text().strip()
        try:
            rows = self.controller.search_history(query)
        except Exception as exc:
            self.statusBar().showMessage(f"Could not load history: {exc}", 5000)
            return
        statistics = getattr(self.controller, "history_statistics", None)
        if callable(statistics):
            try:
                value = statistics()
                minutes = value.dictated_seconds / 60
                self.history_statistics.setText(
                    f"{value.transcript_count} dictations · {value.word_count} words · "
                    f"{minutes:.1f} minutes"
                )
            except Exception:
                self.history_statistics.clear()
        self._refresh_history_filters(rows)
        mode_filter = self.history_mode_filter.currentData()
        provider_filter = self.history_provider_filter.currentData()
        if mode_filter:
            rows = tuple(row for row in rows if getattr(row, "mode_id", "raw") == mode_filter)
        if provider_filter:
            rows = tuple(row for row in rows if row.provider == provider_filter)
        self.history_list.clear()
        for row in rows:
            text = row.final_text.strip() or row.raw_text.strip() or "(empty transcript)"
            preview = text if len(text) <= 180 else f"{text[:177]}…"
            stamp = row.created_at.astimezone().strftime("%Y-%m-%d  %H:%M")
            mode = getattr(row, "mode_id", "raw")
            latency = getattr(row, "latency_ms", None)
            latency_note = f"  ·  {latency} ms" if latency is not None else ""
            meta = (
                f"{stamp}  ·  {mode}  ·  {row.provider}  ·  "
                f"{row.duration_seconds:.1f}s{latency_note}"
            )
            item = QListWidgetItem(f"{preview}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
            self.history_list.addItem(item)
        if not rows:
            item = QListWidgetItem("No transcripts match your search.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.history_list.addItem(item)

    def _refresh_history_filters(self, rows: Sequence[HistoryRow]) -> None:
        mode = self.history_mode_filter.currentData()
        provider = self.history_provider_filter.currentData()
        self.history_mode_filter.blockSignals(True)
        self.history_provider_filter.blockSignals(True)
        self.history_mode_filter.clear()
        self.history_mode_filter.addItem("All modes", "")
        for value in sorted({getattr(row, "mode_id", "raw") for row in rows}):
            self.history_mode_filter.addItem(value.replace("-", " ").title(), value)
        self.history_provider_filter.clear()
        self.history_provider_filter.addItem("All providers", "")
        for value in sorted({row.provider for row in rows}):
            self.history_provider_filter.addItem(value, value)
        self._find_data(self.history_mode_filter, mode)
        self._find_data(self.history_provider_filter, provider)
        self.history_mode_filter.blockSignals(False)
        self.history_provider_filter.blockSignals(False)

    def _history_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        row = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(row, HistoryRow):
            self.history_detail.clear()
            return
        final = row.final_text or "(raw transcript used)"
        self.history_detail.setPlainText(f"Raw\n{row.raw_text}\n\nFinal\n{final}")

    def _selected_history_row(self) -> HistoryRow | None:
        item = self.history_list.currentItem()
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return row if isinstance(row, HistoryRow) else None

    def _history_transform(self) -> None:
        row = self._selected_history_row()
        if row is None:
            return
        self.transform_text.setPlainText(row.final_text or row.raw_text)
        self._transform_target_external = False
        self.select_page(5)

    def _copy_history_item(self, item: QListWidgetItem | None) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(row, HistoryRow):
            return
        self.controller.copy_text(row.final_text or row.raw_text)
        self.statusBar().showMessage("Transcript copied", 2500)

    def _delete_history_item(self) -> None:
        row = self._selected_history_row()
        if row is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete transcript",
                "Delete this transcript and any retained audio now?",
                QMessageBox.StandardButton.Delete | QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Delete
        ):
            return
        callback = getattr(self.controller, "delete_history", None)
        if not callable(callback):
            self.statusBar().showMessage(
                "History deletion will be available after runtime migration", 4000
            )
            return
        try:
            callback(row.id)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete transcript", str(exc))
            return
        self.refresh_history()

    def _clear_history(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Clear history",
                "Delete every transcript and all retained audio now?",
                QMessageBox.StandardButton.Delete | QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Delete
        ):
            return
        callback = getattr(self.controller, "clear_history", None)
        if not callable(callback):
            return
        try:
            callback()
        except Exception as exc:
            QMessageBox.warning(self, "Could not clear history", str(exc))
            return
        self.refresh_history()

    def _history_action(self, method: str) -> None:
        row = self._selected_history_row()
        if row is None:
            return
        callback = getattr(self.controller, method, None)
        if not callable(callback):
            self.statusBar().showMessage(
                "This action will be available after runtime migration", 4000
            )
            return
        try:
            callback(row.id)
        except Exception as exc:
            QMessageBox.warning(self, "History action failed", str(exc))
            return
        self.statusBar().showMessage("History action started", 3000)

    def handle_event(self, event: str, payload: Mapping[str, Any] | None = None) -> None:
        payload = payload or {}
        if event == "state":
            state = str(payload.get("state", "idle"))
            labels = {
                "idle": "Ready",
                "recording": "Listening…",
                "processing": "Transcribing…",
                "error": "Needs attention",
            }
            self.dictation_status.setText(labels.get(state, state.title()))
            self.record_button.setProperty("recording", state == "recording")
            self.record_button.style().unpolish(self.record_button)
            self.record_button.style().polish(self.record_button)
            self.record_button.setEnabled(state != "processing")
        elif event in {"partial", "transcript"}:
            self.live_preview.setText(str(payload.get("text", "")))
            if event == "transcript":
                self.refresh_history()
        elif event == "warning":
            self.statusBar().showMessage(str(payload.get("message", "Warning")), 7000)
        elif event == "error":
            message = str(payload.get("message", "An unexpected error occurred"))
            self.statusBar().showMessage(message, 7000)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.close_to_tray:
            event.ignore()
            self.hide()
            return
        self.controller.shutdown()
        event.accept()
