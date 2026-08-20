use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Raw,
    Clean,
    Code,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DeliveryTarget {
    pub application_id: String,
    pub window_id: String,
    pub title_fingerprint: String,
    pub captured_at: DateTime<Utc>,
}

impl DeliveryTarget {
    pub fn still_matches(&self, current: &Self) -> bool {
        self.application_id == current.application_id
            && self.window_id == current.window_id
            && self.title_fingerprint == current.title_fingerprint
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum CaptureState {
    Idle,
    Capturing {
        session_id: Uuid,
        generation: u64,
        started_at: DateTime<Utc>,
        mode: Mode,
        target: Option<DeliveryTarget>,
    },
    Transcribing {
        session_id: Uuid,
        generation: u64,
        mode: Mode,
        target: Option<DeliveryTarget>,
    },
    Processing {
        session_id: Uuid,
        generation: u64,
        mode: Mode,
        target: Option<DeliveryTarget>,
    },
    Delivering {
        session_id: Uuid,
        generation: u64,
        target: Option<DeliveryTarget>,
    },
    Failed {
        session_id: Option<Uuid>,
        generation: u64,
        message: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureCommand {
    Start,
    Stop,
    Toggle,
    Cancel,
    BeginDelivery,
    BeginProcessing,
    Complete,
    Fail,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum TransitionError {
    #[error("a capture is already active")]
    AlreadyActive,
    #[error("no capture is active")]
    NotActive,
    #[error("invalid transition from {from} using {command}")]
    Invalid {
        from: &'static str,
        command: &'static str,
    },
}

#[derive(Debug, Clone)]
pub struct CaptureCoordinator {
    state: CaptureState,
    generation: u64,
}

impl Default for CaptureCoordinator {
    fn default() -> Self {
        Self {
            state: CaptureState::Idle,
            generation: 0,
        }
    }
}

impl CaptureCoordinator {
    pub fn state(&self) -> &CaptureState {
        &self.state
    }

    pub fn sequence(&self) -> u64 {
        self.generation
    }

    pub fn start(
        &mut self,
        mode: Mode,
        target: Option<DeliveryTarget>,
    ) -> Result<Uuid, TransitionError> {
        if !matches!(self.state, CaptureState::Idle | CaptureState::Failed { .. }) {
            return Err(TransitionError::AlreadyActive);
        }
        self.start_session(Uuid::new_v4(), mode, target)
    }

    pub fn start_session(
        &mut self,
        session_id: Uuid,
        mode: Mode,
        target: Option<DeliveryTarget>,
    ) -> Result<Uuid, TransitionError> {
        if !matches!(self.state, CaptureState::Idle | CaptureState::Failed { .. }) {
            return Err(TransitionError::AlreadyActive);
        }
        self.generation += 1;
        self.state = CaptureState::Capturing {
            session_id,
            generation: self.generation,
            started_at: Utc::now(),
            mode,
            target,
        };
        Ok(session_id)
    }

    pub fn stop(&mut self) -> Result<u64, TransitionError> {
        let CaptureState::Capturing {
            session_id,
            generation,
            mode,
            target,
            ..
        } = self.state.clone()
        else {
            return Err(TransitionError::NotActive);
        };
        self.state = CaptureState::Transcribing {
            session_id,
            generation,
            mode,
            target,
        };
        Ok(generation)
    }

    pub fn toggle(
        &mut self,
        mode: Mode,
        target: Option<DeliveryTarget>,
    ) -> Result<Option<Uuid>, TransitionError> {
        match self.state {
            CaptureState::Idle | CaptureState::Failed { .. } => self.start(mode, target).map(Some),
            CaptureState::Capturing { .. } => self.stop().map(|_| None),
            _ => Err(TransitionError::AlreadyActive),
        }
    }

    pub fn begin_delivery(&mut self, generation: u64) -> Result<(), TransitionError> {
        let CaptureState::Processing {
            session_id,
            generation: active,
            target,
            ..
        } = self.state.clone()
        else {
            return Err(TransitionError::Invalid {
                from: self.phase(),
                command: "begin_delivery",
            });
        };
        if active != generation {
            return Err(TransitionError::Invalid {
                from: "stale_generation",
                command: "begin_delivery",
            });
        }
        self.state = CaptureState::Delivering {
            session_id,
            generation,
            target,
        };
        Ok(())
    }

    pub fn begin_processing(&mut self, generation: u64) -> Result<(), TransitionError> {
        let CaptureState::Transcribing {
            session_id,
            generation: active,
            mode,
            target,
        } = self.state.clone()
        else {
            return Err(TransitionError::Invalid {
                from: self.phase(),
                command: "begin_processing",
            });
        };
        if active != generation {
            return Err(TransitionError::Invalid {
                from: "stale_generation",
                command: "begin_processing",
            });
        }
        self.state = CaptureState::Processing {
            session_id,
            generation,
            mode,
            target,
        };
        Ok(())
    }

    pub fn complete(&mut self, generation: u64) -> bool {
        let is_current = matches!(
            self.state,
            CaptureState::Transcribing { generation: active, .. }
                | CaptureState::Processing { generation: active, .. }
                | CaptureState::Delivering { generation: active, .. } if active == generation
        );
        if is_current {
            self.state = CaptureState::Idle;
        }
        is_current
    }

    pub fn cancel(&mut self) -> Result<(), TransitionError> {
        if matches!(self.state, CaptureState::Idle) {
            return Err(TransitionError::NotActive);
        }
        self.generation += 1;
        self.state = CaptureState::Idle;
        Ok(())
    }

    pub fn fail(&mut self, message: impl Into<String>) {
        let session_id = match self.state {
            CaptureState::Idle => None,
            CaptureState::Capturing { session_id, .. }
            | CaptureState::Transcribing { session_id, .. }
            | CaptureState::Processing { session_id, .. }
            | CaptureState::Delivering { session_id, .. } => Some(session_id),
            CaptureState::Failed { session_id, .. } => session_id,
        };
        self.state = CaptureState::Failed {
            session_id,
            generation: self.generation,
            message: message.into(),
        };
    }

    fn phase(&self) -> &'static str {
        match self.state {
            CaptureState::Idle => "idle",
            CaptureState::Capturing { .. } => "capturing",
            CaptureState::Transcribing { .. } => "transcribing",
            CaptureState::Processing { .. } => "processing",
            CaptureState::Delivering { .. } => "delivering",
            CaptureState::Failed { .. } => "failed",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enforces_one_global_capture() {
        let mut state = CaptureCoordinator::default();
        state.start(Mode::Raw, None).unwrap();
        assert_eq!(
            state.start(Mode::Clean, None),
            Err(TransitionError::AlreadyActive)
        );
    }

    #[test]
    fn cancellation_invalidates_worker_generation() {
        let mut state = CaptureCoordinator::default();
        state.start(Mode::Raw, None).unwrap();
        let generation = state.stop().unwrap();
        state.cancel().unwrap();
        assert!(!state.complete(generation));
        assert!(matches!(state.state(), CaptureState::Idle));
    }

    #[test]
    fn target_must_still_match_capture_start() {
        let target = DeliveryTarget {
            application_id: "code".into(),
            window_id: "42".into(),
            title_fingerprint: "editor".into(),
            captured_at: Utc::now(),
        };
        let mut changed = target.clone();
        changed.window_id = "43".into();
        assert!(!target.still_matches(&changed));
    }
}
