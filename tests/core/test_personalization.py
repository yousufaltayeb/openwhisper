from __future__ import annotations

import json

import pytest

from openwhisper.core.personalization import (
    ActivationRule,
    CommandValidator,
    ConfigurationProposal,
    ContextPolicy,
    ContextSource,
    DictationContext,
    HistoryQuery,
    ModeDefinition,
    ModeRouter,
    Snippet,
    SQLitePersonalizationStore,
    TextOutput,
    TransformDefinition,
    TransformEngine,
    TransformKind,
    VocabularyEntry,
    apply_snippets,
    apply_vocabulary,
    smart_format,
)


def test_modes_are_context_off_by_default_and_route_only_on_matching_activation() -> None:
    message = ModeDefinition(
        "work-message",
        "Work message",
        activation_rules=(ActivationRule(application_pattern="Thunderbird"),),
    )
    router = ModeRouter((ModeDefinition("raw", "Raw"), message))

    assert router.route(application="Firefox").id == "raw"
    assert router.route(application="Mozilla Thunderbird").id == "work-message"
    assert message.context_policy.badge() == "Context off"
    with pytest.raises(ValueError, match="cloud context"):
        ContextPolicy(allow_cloud=True)


def test_context_requires_per_mode_consent_and_never_returns_history_content() -> None:
    context = DictationContext(
        application_name="Mail",
        selected_text="private selection",
        surrounding_text="private surroundings",
        recent_clipboard="private clipboard",
    )
    policy = ContextPolicy(frozenset({ContextSource.APPLICATION, ContextSource.SELECTED_TEXT}))

    assert context.content_for(ContextPolicy()) == {}
    assert context.content_for(policy) == {
        ContextSource.APPLICATION: "Mail",
        ContextSource.SELECTED_TEXT: "private selection",
    }
    assert context.content_for(policy, cloud=True) == {}
    assert context.history_metadata(policy) == ("application", "selected_text")


def test_vocabulary_snippets_and_smart_format_preserve_arabic_english_code_switching() -> None:
    vocabulary = VocabularyEntry("1", "OpenWhisper", ("open whisper", "اوبن ويسبر"))
    snippet = Snippet("1", "my email", "me@example.test")
    text = "um افتح open whisper comma my email new line hello delete that world"

    formatted = smart_format(apply_snippets(apply_vocabulary(text, (vocabulary,)), (snippet,)))

    assert "OpenWhisper, me@example.test" in formatted
    assert "World" in formatted
    assert "hello" not in formatted.casefold()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("hello delete that world", "World"),
        ("كلمة احذف ذلك بديل", "بديل"),
    ],
)
def test_smart_format_backtracks_only_the_immediately_preceding_token(
    source: str, expected: str
) -> None:
    assert smart_format(source) == expected


def test_smart_format_supports_spoken_arabic_and_english_list_items() -> None:
    assert smart_format("first bullet point second نقطة تعداد الثالث") == (
        "First\n• second\n• الثالث"
    )


def test_preview_apply_undo_never_overwrites_text_changed_after_apply() -> None:
    engine = TransformEngine()
    transform = TransformDefinition("polish-test", "Polish", TransformKind.POLISH)
    preview = engine.preview("hello comma world", transform)

    applied = engine.apply(preview)
    assert applied == "Hello, world"
    assert engine.undo("edited elsewhere") == "edited elsewhere"
    assert engine.undo(applied) == "hello comma world"


def test_commands_validate_proposals_without_applying_them() -> None:
    validator = CommandValidator()

    outcome = validator.validate("switch mode to Formal / MSA")
    assert isinstance(outcome, ConfigurationProposal)
    assert outcome.requires_confirmation
    assert isinstance(validator.validate("search history invoice"), HistoryQuery)
    assert isinstance(validator.validate("generate a short subject line"), TextOutput)
    with pytest.raises(ValueError, match="select text"):
        validator.validate("rewrite selection formal")


def test_personalization_store_persists_modes_rules_and_csv_json_vocabulary(tmp_path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "personalization.sqlite3")
    try:
        mode = ModeDefinition(
            "email-client",
            "Email client",
            transcription_provider="faster-whisper",
            transcription_model="medium",
            cleanup_provider="openai",
            cleanup_model="gpt-4.1-mini",
            activation_rules=(ActivationRule(application_pattern="Thunderbird"),),
        )
        store.save_mode(mode)
        assert store.schema_version == 1
        persisted = next(item for item in store.modes() if item.id == mode.id)
        assert persisted.activation_rules == mode.activation_rules
        assert persisted.transcription_model == "medium"
        assert persisted.cleanup_model == "gpt-4.1-mini"

        entry = VocabularyEntry("project", "OpenWhisper", ("open whisper",), "en")
        store.save_vocabulary(entry)
        exported = store.export_vocabulary(format="json")
        assert json.loads(exported)[0]["written_form"] == "OpenWhisper"
        store.import_vocabulary(
            "id,written_form,spoken_forms,language,case_sensitive\n"
            "other,رياض,riyadh | riyad,ar,false\n",
            format="csv",
        )
        assert {item.id for item in store.vocabulary()} == {"project", "other"}

        store.import_snippets(
            "id,trigger,expansion,language,enabled\n"
            "signature,my signature,Best regards,auto,true\n",
            format="csv",
        )
        snippets_json = store.export_snippets(format="json")
        assert json.loads(snippets_json)[0]["expansion"] == "Best regards"
    finally:
        store.close()
