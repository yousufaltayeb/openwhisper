use openwhisper_core::processing::TextProcessor;
use openwhisper_core::{CaptureCoordinator, CaptureState, Mode};
use serde::Deserialize;

#[derive(Deserialize)]
struct TextFixture {
    cases: Vec<TextCase>,
}

#[derive(Deserialize)]
struct TextCase {
    mode: String,
    input: String,
    output: String,
}

#[test]
fn frozen_text_processing_contract() {
    let fixture: TextFixture = serde_json::from_str(include_str!(
        "../../../fixtures/golden/text-processing.json"
    ))
    .unwrap();
    for case in fixture.cases {
        let mode = match case.mode.as_str() {
            "raw" => Mode::Raw,
            "clean" => Mode::Clean,
            "code" => Mode::Code,
            _ => panic!("unknown fixture mode"),
        };
        assert_eq!(
            TextProcessor::default().process(&case.input, mode),
            case.output
        );
    }
}

#[test]
fn stale_generation_cannot_complete_a_new_session() {
    let mut coordinator = CaptureCoordinator::default();
    coordinator.start(Mode::Raw, None).unwrap();
    let stale = coordinator.stop().unwrap();
    coordinator.cancel().unwrap();
    coordinator.start(Mode::Raw, None).unwrap();
    assert!(!coordinator.complete(stale));
    assert!(matches!(
        coordinator.state(),
        CaptureState::Capturing { .. }
    ));
}
