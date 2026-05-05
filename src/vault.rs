use std::{fs, path::PathBuf};

use crate::{
    config::{load_runtime_config, save_config, RuntimeConfig},
    error::Result,
};

#[derive(Debug, Clone)]
pub struct SetupOptions {
    pub home: Option<PathBuf>,
    pub dry_run: bool,
}

pub fn setup_home(options: SetupOptions) -> Result<RuntimeConfig> {
    let config = load_runtime_config(options.home)?;
    let directories = managed_directories(&config);

    if options.dry_run {
        return Ok(config);
    }

    for directory in directories {
        fs::create_dir_all(directory)?;
    }

    if !config.config_path().is_file() {
        save_config(&config)?;
    }

    Ok(config)
}

pub fn managed_directories(config: &RuntimeConfig) -> Vec<PathBuf> {
    vec![
        config.vault_path.clone(),
        config.vault_path.join("raw").join("inbox"),
        config.vault_path.join("raw").join("processed"),
        config.vault_path.join("raw").join("quarantine"),
        config.vault_path.join("Sources"),
        config.vault_path.join("Memories").join("facts"),
        config.vault_path.join("Memories").join("preferences"),
        config.vault_path.join("Memories").join("decisions"),
        config.vault_path.join("Memories").join("context"),
        config.vault_path.join("Memories").join("tasks"),
        config.vault_path.join("Memories").join("conversations"),
        config.vault_path.join("Wiki").join("sources"),
        config.vault_path.join("Wiki").join("entities"),
        config.vault_path.join("Wiki").join("concepts"),
        config.vault_path.join("Wiki").join("syntheses"),
        config.state_path(),
        config.state_path().join("cache"),
        config.state_path().join("embeddings"),
        config.state_path().join("locks"),
    ]
}

pub fn status(config: &RuntimeConfig) -> Vec<(String, String)> {
    vec![
        ("home".to_string(), config.home_path.display().to_string()),
        ("vault".to_string(), config.vault_path.display().to_string()),
        (
            "config".to_string(),
            config.config_path().display().to_string(),
        ),
        (
            "index".to_string(),
            config.index_path().display().to_string(),
        ),
        (
            "agent_enabled".to_string(),
            config.file.agent_policy.enabled.to_string(),
        ),
        (
            "auto_recall".to_string(),
            config.file.agent_policy.auto_recall.to_string(),
        ),
        (
            "aliases".to_string(),
            config.file.agent_policy.aliases.join(", "),
        ),
    ]
}
