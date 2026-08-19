use crate::state::Mode;

#[derive(Debug, Clone, Default)]
pub struct TextProcessor {
    replacements: Vec<(String, String)>,
}

impl TextProcessor {
    pub fn with_replacements(replacements: Vec<(String, String)>) -> Self {
        Self { replacements }
    }

    pub fn process(&self, input: &str, mode: Mode) -> String {
        let normalized = match mode {
            Mode::Code => normalize_code(input),
            Mode::Raw => input.trim().to_owned(),
            Mode::Clean => normalize_prose(input),
        };
        self.replacements
            .iter()
            .fold(normalized, |text, (from, to)| text.replace(from, to))
    }
}

fn normalize_prose(input: &str) -> String {
    input.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn normalize_code(input: &str) -> String {
    input
        .lines()
        .map(str::trim_end)
        .collect::<Vec<_>>()
        .join("\n")
        .trim()
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixed_arabic_english_stays_in_logical_unicode_order() {
        let input = "شغّل  cargo   test من فضلك";
        let result = TextProcessor::default().process(input, Mode::Clean);
        assert_eq!(result, "شغّل cargo test من فضلك");
        assert_eq!(result.as_bytes(), "شغّل cargo test من فضلك".as_bytes());
    }

    #[test]
    fn replacements_are_deterministic() {
        let processor = TextProcessor::with_replacements(vec![("جت هب".into(), "GitHub".into())]);
        assert_eq!(processor.process("افتح جت هب", Mode::Clean), "افتح GitHub");
    }
}
