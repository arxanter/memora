use std::{
    env, fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::error::{MemoraError, Result};

pub const CONFIG_FILE_NAME: &str = "config.yaml";
pub const DEFAULT_HOME_DIR_NAME: &str = "memora";
pub const DEFAULT_VAULT_DIR_NAME: &str = "vault";
pub const DEFAULT_SCHEMA_VERSION: u16 = 1;
const ENV_SEMANTIC_PROVIDER: &str = "MEMORA_SEMANTIC_PROVIDER";
const ENV_SEMANTIC_MODEL: &str = "MEMORA_SEMANTIC_MODEL";
const ENV_SEMANTIC_COMMAND: &str = "MEMORA_SEMANTIC_COMMAND";

#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    pub home_path: PathBuf,
    pub vault_path: PathBuf,
    pub file: ConfigFile,
}

impl RuntimeConfig {
    pub fn config_path(&self) -> PathBuf {
        self.home_path.join(CONFIG_FILE_NAME)
    }

    pub fn state_path(&self) -> PathBuf {
        self.home_path.join("state")
    }

    pub fn index_path(&self) -> PathBuf {
        self.state_path().join("index.sqlite")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigFile {
    pub schema_version: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_project: Option<String>,
    #[serde(default)]
    pub semantic: SemanticConfig,
    #[serde(default)]
    pub agent_policy: AgentPolicyConfig,
}

impl Default for ConfigFile {
    fn default() -> Self {
        Self {
            schema_version: DEFAULT_SCHEMA_VERSION,
            default_project: None,
            semantic: SemanticConfig::default(),
            agent_policy: AgentPolicyConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SemanticConfig {
    #[serde(
        default = "default_semantic_provider",
        skip_serializing_if = "Option::is_none"
    )]
    pub provider: Option<String>,
    #[serde(default = "default_semantic_model")]
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command: Option<Vec<String>>,
}

impl Default for SemanticConfig {
    fn default() -> Self {
        Self {
            provider: default_semantic_provider(),
            model: default_semantic_model(),
            command: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentPolicyConfig {
    #[serde(default = "default_aliases")]
    pub aliases: Vec<String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_true")]
    pub auto_recall: bool,
    #[serde(default = "default_min_active_confidence")]
    pub min_active_confidence: f32,
    #[serde(default = "default_min_pending_confidence")]
    pub min_pending_confidence: f32,
}

impl Default for AgentPolicyConfig {
    fn default() -> Self {
        Self {
            aliases: default_aliases(),
            enabled: true,
            auto_recall: true,
            min_active_confidence: default_min_active_confidence(),
            min_pending_confidence: default_min_pending_confidence(),
        }
    }
}

pub fn resolve_home(home: Option<PathBuf>) -> Result<PathBuf> {
    if let Some(path) = home {
        return Ok(expand_home(path));
    }

    if let Some(path) = env::var_os("MEMORA_HOME") {
        return Ok(expand_home(PathBuf::from(path)));
    }

    let user_home = env::var_os("HOME").ok_or(MemoraError::HomeNotFound)?;
    Ok(PathBuf::from(user_home).join(DEFAULT_HOME_DIR_NAME))
}

pub fn load_runtime_config(home: Option<PathBuf>) -> Result<RuntimeConfig> {
    let home_path = resolve_home(home)?;
    let config_path = home_path.join(CONFIG_FILE_NAME);
    let mut file = if config_path.is_file() {
        let raw = fs::read_to_string(&config_path)?;
        serde_yaml::from_str::<ConfigFile>(&raw)?
    } else {
        ConfigFile::default()
    };
    apply_env_overrides(&mut file);

    validate_config(&file)?;
    Ok(RuntimeConfig {
        vault_path: home_path.join(DEFAULT_VAULT_DIR_NAME),
        home_path,
        file,
    })
}

pub fn save_config(config: &RuntimeConfig) -> Result<()> {
    fs::create_dir_all(&config.home_path)?;
    let raw = serde_yaml::to_string(&config.file)?;
    fs::write(config.config_path(), raw)?;
    Ok(())
}

pub fn set_aliases(config: &mut RuntimeConfig, aliases: Vec<String>) -> Result<()> {
    let normalized = normalize_aliases(aliases)?;
    config.file.agent_policy.aliases = normalized;
    save_config(config)
}

fn validate_config(file: &ConfigFile) -> Result<()> {
    if file.schema_version != DEFAULT_SCHEMA_VERSION {
        return Err(MemoraError::InvalidArgument(format!(
            "schema_version must be {DEFAULT_SCHEMA_VERSION}"
        )));
    }

    if file.agent_policy.aliases.is_empty() {
        return Err(MemoraError::InvalidArgument(
            "agent_policy.aliases must include at least one alias".to_string(),
        ));
    }

    if file.agent_policy.min_active_confidence < file.agent_policy.min_pending_confidence {
        return Err(MemoraError::InvalidArgument(
            "min_active_confidence must be greater than or equal to min_pending_confidence"
                .to_string(),
        ));
    }

    Ok(())
}

fn apply_env_overrides(file: &mut ConfigFile) {
    if let Ok(provider) = env::var(ENV_SEMANTIC_PROVIDER) {
        let provider = provider.trim();
        file.semantic.provider = if provider.eq_ignore_ascii_case("none") || provider.is_empty() {
            None
        } else {
            Some(provider.to_string())
        };
    }
    if let Ok(model) = env::var(ENV_SEMANTIC_MODEL) {
        if !model.trim().is_empty() {
            file.semantic.model = model.trim().to_string();
        }
    }
    if let Ok(command) = env::var(ENV_SEMANTIC_COMMAND) {
        let command: Vec<String> = command
            .split_whitespace()
            .map(str::trim)
            .filter(|part| !part.is_empty())
            .map(str::to_string)
            .collect();
        file.semantic.command = if command.is_empty() {
            None
        } else {
            Some(command)
        };
    }
}

fn normalize_aliases(aliases: Vec<String>) -> Result<Vec<String>> {
    let mut normalized = Vec::new();
    for alias in aliases {
        let trimmed = alias.trim();
        if trimmed.is_empty() {
            continue;
        }
        if normalized
            .iter()
            .any(|existing: &String| existing.eq_ignore_ascii_case(trimmed))
        {
            continue;
        }
        normalized.push(trimmed.to_string());
    }

    if normalized.is_empty() {
        return Err(MemoraError::InvalidArgument(
            "at least one alias is required".to_string(),
        ));
    }

    Ok(normalized)
}

fn expand_home(path: PathBuf) -> PathBuf {
    let raw = path.to_string_lossy();
    if raw == "~" {
        if let Some(home) = env::var_os("HOME") {
            return PathBuf::from(home);
        }
    }
    if let Some(stripped) = raw.strip_prefix("~/") {
        if let Some(home) = env::var_os("HOME") {
            return Path::new(&home).join(stripped);
        }
    }
    path
}

fn default_aliases() -> Vec<String> {
    vec!["Remi".to_string(), "Рэми".to_string(), "Реми".to_string()]
}

fn default_true() -> bool {
    true
}

fn default_semantic_provider() -> Option<String> {
    Some("fastembed".to_string())
}

fn default_semantic_model() -> String {
    "AllMiniLML6V2".to_string()
}

fn default_min_active_confidence() -> f32 {
    0.85
}

fn default_min_pending_confidence() -> f32 {
    0.55
}
