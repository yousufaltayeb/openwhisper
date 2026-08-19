use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderId {
    Local,
    Cohere,
    OpenAi,
    Groq,
    Deepgram,
}

impl ProviderId {
    pub const fn is_networked(self) -> bool {
        !matches!(self, Self::Local)
    }

    pub const fn credential_service(self) -> Option<&'static str> {
        match self {
            Self::Local => None,
            Self::Cohere => Some("cohere"),
            Self::OpenAi => Some("openai"),
            Self::Groq => Some("groq"),
            Self::Deepgram => Some("deepgram"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProviderPolicy {
    pub local_only: bool,
    pub secure_store_available: bool,
    pub encrypted_fallback_enabled: bool,
    pub explicit_network_action: bool,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ProviderPolicyError {
    #[error("local-only mode prohibits network providers")]
    LocalOnly,
    #[error("cloud access requires an explicit user action")]
    ExplicitActionRequired,
    #[error("no approved credential store is available")]
    SecureStoreUnavailable,
}

impl ProviderPolicy {
    pub fn authorize(&self, provider: ProviderId) -> Result<(), ProviderPolicyError> {
        if !provider.is_networked() {
            return Ok(());
        }
        if self.local_only {
            return Err(ProviderPolicyError::LocalOnly);
        }
        if !self.explicit_network_action {
            return Err(ProviderPolicyError::ExplicitActionRequired);
        }
        if !self.secure_store_available && !self.encrypted_fallback_enabled {
            return Err(ProviderPolicyError::SecureStoreUnavailable);
        }
        Ok(())
    }
}

pub trait SecretStore: Send + Sync {
    fn available(&self) -> bool;
    fn set(&self, service: &str, secret: &[u8]) -> Result<(), String>;
    fn get(&self, service: &str) -> Result<Option<Vec<u8>>, String>;
    fn delete(&self, service: &str) -> Result<(), String>;
}

#[derive(Debug, Default)]
pub struct UnavailableSecretStore;

impl SecretStore for UnavailableSecretStore {
    fn available(&self) -> bool {
        false
    }
    fn set(&self, _service: &str, _secret: &[u8]) -> Result<(), String> {
        Err("secure store unavailable".into())
    }
    fn get(&self, _service: &str) -> Result<Option<Vec<u8>>, String> {
        Err("secure store unavailable".into())
    }
    fn delete(&self, _service: &str) -> Result<(), String> {
        Err("secure store unavailable".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_only_is_a_zero_network_gate() {
        let policy = ProviderPolicy {
            local_only: true,
            secure_store_available: true,
            encrypted_fallback_enabled: false,
            explicit_network_action: true,
        };
        assert_eq!(
            policy.authorize(ProviderId::OpenAi),
            Err(ProviderPolicyError::LocalOnly)
        );
        assert_eq!(policy.authorize(ProviderId::Local), Ok(()));
    }

    #[test]
    fn cloud_requires_consent_and_credential_protection() {
        let policy = ProviderPolicy {
            local_only: false,
            secure_store_available: false,
            encrypted_fallback_enabled: false,
            explicit_network_action: true,
        };
        assert_eq!(
            policy.authorize(ProviderId::Deepgram),
            Err(ProviderPolicyError::SecureStoreUnavailable)
        );
    }
}
