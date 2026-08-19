use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClipboardWrite {
    pub previous_value: Option<String>,
    pub temporary_value: String,
    pub sequence_after_write: u64,
}

impl ClipboardWrite {
    pub fn can_restore(&self, current_sequence: u64, current_value: Option<&str>) -> bool {
        current_sequence == self.sequence_after_write
            && current_value == Some(self.temporary_value.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restores_only_when_openwhisper_still_owns_clipboard() {
        let write = ClipboardWrite {
            previous_value: Some("before".into()),
            temporary_value: "dictated".into(),
            sequence_after_write: 9,
        };
        assert!(write.can_restore(9, Some("dictated")));
        assert!(!write.can_restore(10, Some("dictated")));
        assert!(!write.can_restore(9, Some("user copied this")));
    }
}
