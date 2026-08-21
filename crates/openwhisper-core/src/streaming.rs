use crate::{Mode, processing::TextProcessor};

pub const STREAM_BATCH_BYTES: usize = 16_000 * 2 * 300 / 1_000;
pub const ROLLING_WINDOW_BYTES: usize = 16_000 * 2 * 15;

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StabilizedUpdate {
    pub committed_delta: String,
    pub committed: String,
    pub preview: String,
}

/// Word-prefix stabilizer used by both the daemon and deterministic tests.
/// A word is committed only after it appears in the same position in two
/// consecutive hypotheses. The final hypothesis is flushed explicitly.
#[derive(Debug, Clone, Default)]
pub struct TranscriptStabilizer {
    committed_words: Vec<String>,
    previous_words: Vec<String>,
}

impl TranscriptStabilizer {
    pub fn update(&mut self, hypothesis: &str) -> StabilizedUpdate {
        let words = self.uncommitted_words(hypothesis);
        let shared = self
            .previous_words
            .iter()
            .zip(&words)
            .take_while(|(left, right)| equivalent_word(left, right))
            .count();
        let delta_words = words[..shared].to_vec();
        self.committed_words.extend(delta_words.iter().cloned());
        self.previous_words = words[shared..].to_vec();
        self.render(delta_words)
    }

    pub fn finish(&mut self, hypothesis: Option<&str>) -> StabilizedUpdate {
        if let Some(hypothesis) = hypothesis {
            self.previous_words = self.uncommitted_words(hypothesis);
        }
        let delta = std::mem::take(&mut self.previous_words);
        self.committed_words.extend(delta.iter().cloned());
        self.render(delta)
    }

    pub fn committed(&self) -> String {
        self.committed_words.join(" ")
    }

    pub fn preview(&self) -> String {
        self.previous_words.join(" ")
    }

    pub fn reset_unstable(&mut self) {
        self.previous_words.clear();
    }

    fn render(&self, delta_words: Vec<String>) -> StabilizedUpdate {
        StabilizedUpdate {
            committed_delta: delta_words.join(" "),
            committed: self.committed(),
            preview: self.previous_words.join(" "),
        }
    }

    fn uncommitted_words(&self, hypothesis: &str) -> Vec<String> {
        let hypothesis = words(hypothesis);
        let shared_prefix = self
            .committed_words
            .iter()
            .zip(&hypothesis)
            .take_while(|(left, right)| equivalent_word(left, right))
            .count();
        let overlap = (0..=self.committed_words.len().min(hypothesis.len()))
            .rev()
            .find(|length| {
                self.committed_words[self.committed_words.len() - length..]
                    .iter()
                    .zip(&hypothesis[..*length])
                    .all(|(left, right)| equivalent_word(left, right))
            })
            .unwrap_or(0);

        // A late full-window hypothesis may revise the last committed token.
        // Committed text is immutable once typed, so keep its position and
        // append only words beyond that prefix instead of duplicating the
        // entire sentence. A hypothesis that starts later in a trimmed window
        // is handled by the suffix overlap above.
        // Prefer this interpretation when a short suffix overlap is ambiguous
        // with the beginning of the full transcript (for example, both start
        // and committed tail are "Open").
        if shared_prefix > 0
            && (shared_prefix >= overlap || hypothesis.len() >= self.committed_words.len())
        {
            return hypothesis[self.committed_words.len().min(hypothesis.len())..].to_vec();
        }
        if overlap > 0 {
            return hypothesis[overlap..].to_vec();
        }
        hypothesis
    }
}

fn words(value: &str) -> Vec<String> {
    value.split_whitespace().map(str::to_owned).collect()
}

fn equivalent_word(left: &str, right: &str) -> bool {
    fn normalized(value: &str) -> String {
        value
            .trim_matches(|character: char| !character.is_alphanumeric())
            .chars()
            .flat_map(char::to_lowercase)
            .collect()
    }
    normalized(left) == normalized(right)
}

/// Bounded audio coalescer. While inference is active, new PCM is merged into
/// one pending rolling window rather than queued as individual requests.
#[derive(Debug, Clone, Default)]
pub struct AudioCoalescer {
    in_flight: bool,
    dirty: bool,
    pending: Vec<u8>,
}

impl AudioCoalescer {
    pub fn append(&mut self, pcm: &[u8]) -> Option<Vec<u8>> {
        self.pending.extend_from_slice(pcm);
        self.dirty = true;
        if self.pending.len() > ROLLING_WINDOW_BYTES {
            let excess = self.pending.len() - ROLLING_WINDOW_BYTES;
            let aligned = excess + (excess % 2);
            self.pending.drain(..aligned.min(self.pending.len()));
        }
        if self.in_flight || self.pending.len() < STREAM_BATCH_BYTES {
            return None;
        }
        self.in_flight = true;
        self.dirty = false;
        Some(self.pending.clone())
    }

    pub fn complete_inference(&mut self) -> Option<Vec<u8>> {
        self.in_flight = false;
        if !self.dirty || self.pending.len() < STREAM_BATCH_BYTES {
            return None;
        }
        self.in_flight = true;
        self.dirty = false;
        Some(self.pending.clone())
    }

    pub fn finish(&mut self) -> Vec<u8> {
        self.in_flight = false;
        std::mem::take(&mut self.pending)
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }
}

/// Incremental processor that keeps a guarded suffix so normalization and
/// configured replacements can span worker chunks. `finish` always makes the
/// concatenated deltas byte-identical to batch processing.
#[derive(Debug, Clone)]
pub struct StreamingTextProcessor {
    processor: TextProcessor,
    mode: Mode,
    raw: String,
    emitted: String,
    guard_words: usize,
}

impl StreamingTextProcessor {
    pub fn new(mode: Mode, replacements: Vec<(String, String)>) -> Self {
        let guard_words = replacements
            .iter()
            .map(|(from, _)| from.split_whitespace().count().max(1))
            .max()
            .unwrap_or(1)
            * 2;
        Self {
            processor: TextProcessor::with_replacements(replacements),
            mode,
            raw: String::new(),
            emitted: String::new(),
            guard_words,
        }
    }

    pub fn push(&mut self, delta: &str) -> String {
        append_unique_words(&mut self.raw, delta);
        let tokens: Vec<&str> = self.raw.split_whitespace().collect();
        if tokens.len() <= self.guard_words {
            return String::new();
        }
        let safe = tokens[..tokens.len() - self.guard_words].join(" ");
        self.emit_prefix(self.processor.process(&safe, self.mode))
    }

    pub fn finish(&mut self, suffix: &str) -> String {
        append_unique_words(&mut self.raw, suffix);
        self.emit_prefix(self.processor.process(&self.raw, self.mode))
    }

    pub fn final_text(&self) -> &str {
        &self.emitted
    }

    fn emit_prefix(&mut self, processed: String) -> String {
        if processed == self.emitted {
            return String::new();
        }
        if let Some(delta) = processed.strip_prefix(&self.emitted) {
            let delta = delta.to_owned();
            self.emitted = processed;
            delta
        } else {
            // A committed prefix must never be rewritten. Hold the changed
            // hypothesis until finish; stabilizer boundaries make this rare.
            String::new()
        }
    }
}

fn append_unique_words(target: &mut String, delta: &str) {
    let delta = delta.trim();
    if delta.is_empty() {
        return;
    }
    let existing_words: Vec<&str> = target.split_whitespace().collect();
    let incoming_words: Vec<&str> = delta.split_whitespace().collect();
    let shared_prefix = existing_words
        .iter()
        .zip(&incoming_words)
        .take_while(|(left, right)| equivalent_word(left, right))
        .count();
    let suffix_overlap = (0..=existing_words.len().min(incoming_words.len()))
        .rev()
        .find(|length| {
            existing_words[existing_words.len() - length..]
                .iter()
                .zip(&incoming_words[..*length])
                .all(|(left, right)| equivalent_word(left, right))
        })
        .unwrap_or(0);
    let skip = if shared_prefix >= 2
        && (incoming_words.len() >= existing_words.len() || shared_prefix == incoming_words.len())
    {
        existing_words.len().min(incoming_words.len())
    } else {
        suffix_overlap
    };
    let delta = incoming_words[skip..].join(" ");
    if delta.is_empty() {
        return;
    }
    if !target.is_empty() {
        target.push(' ');
    }
    target.push_str(&delta);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commits_only_the_two_hypothesis_common_prefix_and_flushes_suffix() {
        let mut stabilizer = TranscriptStabilizer::default();
        assert_eq!(stabilizer.update("hello brave").committed_delta, "");
        let update = stabilizer.update("hello bright world");
        assert_eq!(update.committed_delta, "hello");
        assert_eq!(update.preview, "bright world");
        let update = stabilizer.update("hello bright world today");
        assert_eq!(update.committed_delta, "bright world");
        assert_eq!(stabilizer.finish(Some("today")).committed_delta, "today");
        assert_eq!(stabilizer.committed(), "hello bright world today");
    }

    #[test]
    fn slow_inference_coalesces_into_one_bounded_rolling_window() {
        let mut coalescer = AudioCoalescer::default();
        assert!(coalescer.append(&vec![1; STREAM_BATCH_BYTES]).is_some());
        for _ in 0..100 {
            assert!(coalescer.append(&vec![2; STREAM_BATCH_BYTES]).is_none());
        }
        assert!(coalescer.pending_len() <= ROLLING_WINDOW_BYTES);
        assert_eq!(coalescer.pending_len() % 2, 0);
        assert!(coalescer.complete_inference().is_some());
    }

    #[test]
    fn replaying_the_uncommitted_window_after_a_crash_never_duplicates_commits() {
        let mut stabilizer = TranscriptStabilizer::default();
        stabilizer.update("hello world");
        assert_eq!(stabilizer.update("hello world").committed, "hello world");
        let replay = stabilizer.update("hello world");
        assert!(replay.committed_delta.is_empty());
        stabilizer.update("hello world today");
        let final_update = stabilizer.update("hello world today");
        assert_eq!(final_update.committed_delta, "today");
        assert_eq!(final_update.committed, "hello world today");
    }

    #[test]
    fn surface_case_and_punctuation_drift_never_duplicates_a_committed_prefix() {
        let mut stabilizer = TranscriptStabilizer::default();
        stabilizer.update("Open Whisper Streaming");
        assert_eq!(
            stabilizer
                .update("Open whisper streaming dictation")
                .committed_delta,
            "Open whisper streaming"
        );
        let final_update =
            stabilizer.finish(Some("Open Whisper Streaming Dictation keeps stable words."));
        assert_eq!(
            final_update.committed_delta,
            "Dictation keeps stable words."
        );
        assert_eq!(
            stabilizer.committed(),
            "Open whisper streaming Dictation keeps stable words."
        );
    }

    #[test]
    fn a_revised_committed_tail_is_preserved_without_repeating_the_sentence() {
        let mut stabilizer = TranscriptStabilizer::default();
        stabilizer.update("Open whisper streaming dictation keep stable Pewds");
        stabilizer.update("Open whisper streaming dictation keep stable Pewds");
        let update = stabilizer.finish(Some(
            "Open whisper streaming dictation keep stable words while speaking",
        ));
        assert_eq!(update.committed_delta, "while speaking");
        assert_eq!(
            update.committed,
            "Open whisper streaming dictation keep stable Pewds while speaking"
        );
    }

    #[test]
    fn an_ambiguous_tail_word_does_not_turn_a_full_hypothesis_into_a_suffix() {
        let mut stabilizer = TranscriptStabilizer::default();
        stabilizer.update("Open stable words Open");
        stabilizer.update("Open stable words Open");
        let update = stabilizer.update("Open stable words revised tail");
        assert!(update.committed_delta.is_empty());
        let final_update = stabilizer.finish(Some("Open stable words revised tail"));
        assert_eq!(final_update.committed_delta, "tail");
        assert_eq!(final_update.committed, "Open stable words Open tail");
    }

    #[test]
    fn streaming_replacements_cross_chunks_and_match_batch_unicode_bytes() {
        let replacements = vec![("جت هب".into(), "GitHub".into())];
        let processor = TextProcessor::with_replacements(replacements.clone());
        let mut streaming = StreamingTextProcessor::new(Mode::Clean, replacements);
        let mut output = streaming.push("افتح جت");
        output.push_str(&streaming.push("هب ثم شغّل"));
        output.push_str(&streaming.finish("cargo test"));
        let expected = processor.process("افتح جت هب ثم شغّل cargo test", Mode::Clean);
        assert_eq!(output.as_bytes(), expected.as_bytes());
    }

    #[test]
    fn processor_deduplicates_a_reset_after_an_immutable_revised_tail() {
        let mut streaming = StreamingTextProcessor::new(Mode::Raw, vec![]);
        let mut output = streaming.push("Open whisper streaming keep stable Pewds");
        output.push_str(&streaming.push("Open whisper streaming keep stable words while speaking"));
        output.push_str(&streaming.finish(""));
        assert_eq!(
            output,
            "Open whisper streaming keep stable Pewds while speaking"
        );
    }

    #[test]
    fn every_mode_emits_exactly_its_canonical_batch_bytes() {
        for mode in [Mode::Raw, Mode::Clean, Mode::Code] {
            let replacements = vec![("open whisper".into(), "OpenWhisper".into())];
            let processor = TextProcessor::with_replacements(replacements.clone());
            let mut streaming = StreamingTextProcessor::new(mode, replacements);
            let mut output = streaming.push("شغّل open");
            output.push_str(&streaming.push("whisper ثم cargo"));
            output.push_str(&streaming.finish("test"));
            let expected = processor.process("شغّل open whisper ثم cargo test", mode);
            assert_eq!(output.as_bytes(), expected.as_bytes(), "mode {mode:?}");
        }
    }
}
