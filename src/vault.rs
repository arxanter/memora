use std::{env, fs, path::PathBuf};

use crate::{
    config::{load_runtime_config, save_config, RuntimeConfig},
    error::{MemoraError, Result},
    util::file_hash,
};

#[derive(Debug, Clone)]
pub struct SetupOptions {
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct BinaryInstallOptions {
    pub source: Option<PathBuf>,
    pub expected_sha256: Option<String>,
    pub overwrite: bool,
    pub dry_run: bool,
}

pub fn setup_home(options: SetupOptions) -> Result<RuntimeConfig> {
    let config = load_runtime_config()?;
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
        config.vault_path.join("raw").join("analysis"),
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
        config.home_path.join("bin"),
        config.state_path(),
        config.state_path().join("cache"),
        config.state_path().join("embeddings"),
        config.state_path().join("locks"),
    ]
}

pub fn bin_path(config: &RuntimeConfig) -> PathBuf {
    config.home_path.join("bin").join("memora")
}

pub fn install_binary(config: &RuntimeConfig, options: BinaryInstallOptions) -> Result<PathBuf> {
    let source = options.source.unwrap_or(env::current_exe()?);
    if !source.is_file() {
        return Err(MemoraError::NotFound(source.display().to_string()));
    }
    if let Some(expected) = &options.expected_sha256 {
        let actual = file_hash(&source)?;
        let expected = normalize_sha256(expected);
        if actual != expected {
            return Err(MemoraError::InvalidArgument(format!(
                "sha256 mismatch for {}: expected {}, got {}",
                source.display(),
                expected,
                actual
            )));
        }
    }
    let target = bin_path(config);
    if target.exists() && !options.overwrite {
        return Err(MemoraError::InvalidArgument(format!(
            "{} already exists; pass --force to overwrite",
            target.display()
        )));
    }
    if !options.dry_run {
        let parent = target
            .parent()
            .ok_or_else(|| MemoraError::Message("bin path has no parent".to_string()))?;
        fs::create_dir_all(parent)?;
        let temp_target = parent.join(format!(".memora.tmp-{}", std::process::id()));
        if temp_target.exists() {
            fs::remove_file(&temp_target)?;
        }
        fs::copy(&source, &temp_target)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(&temp_target)?.permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&temp_target, permissions)?;
        }
        #[cfg(windows)]
        if target.exists() {
            fs::remove_file(&target)?;
        }
        if let Err(error) = fs::rename(&temp_target, &target) {
            let _ = fs::remove_file(&temp_target);
            return Err(error.into());
        }
    }
    Ok(target)
}

fn normalize_sha256(value: &str) -> String {
    value
        .trim()
        .trim_start_matches("sha256:")
        .trim()
        .to_ascii_lowercase()
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

pub fn uninstall(
    config: &RuntimeConfig,
    remove_vault: bool,
    dry_run: bool,
) -> Result<Vec<PathBuf>> {
    let mut targets = vec![config.state_path(), bin_path(config)];
    if remove_vault {
        targets.push(config.vault_path.clone());
        targets.push(config.config_path());
    }

    if !dry_run {
        for target in &targets {
            if target.is_dir() {
                fs::remove_dir_all(target)?;
            } else if target.is_file() {
                fs::remove_file(target)?;
            }
        }
        let bin_dir = config.home_path.join("bin");
        if bin_dir.is_dir() && fs::read_dir(&bin_dir)?.next().is_none() {
            fs::remove_dir(bin_dir)?;
        }
    }

    Ok(targets)
}
