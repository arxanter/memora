use std::{fs, path::PathBuf};

use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use crate::{
    config::RuntimeConfig,
    error::Result,
    indexer::{reindex, ReindexStats},
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct TrackedFile {
    path: String,
    mtime_ns: u128,
    size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct FreshnessSnapshot {
    version: u16,
    files: Vec<TrackedFile>,
}

#[derive(Debug, Clone)]
pub struct RefreshOutcome {
    pub checked_files: usize,
    pub reindexed: bool,
    pub reason: String,
    pub stats: Option<ReindexStats>,
}

pub fn refresh_if_needed(config: &RuntimeConfig) -> Result<RefreshOutcome> {
    let state_path = state_path(config);
    let current = scan_snapshot(config)?;
    let previous = read_snapshot(&state_path).ok().flatten();
    let index_missing = !config.index_path().is_file();
    let changed = previous.as_ref() != Some(&current);

    if !index_missing && !changed {
        return Ok(RefreshOutcome {
            checked_files: current.files.len(),
            reindexed: false,
            reason: "fresh".to_string(),
            stats: None,
        });
    }

    let reason = if index_missing {
        "index_missing"
    } else {
        "vault_changed"
    }
    .to_string();
    let stats = reindex(config, false)?;
    write_snapshot(&state_path, &current)?;
    Ok(RefreshOutcome {
        checked_files: current.files.len(),
        reindexed: true,
        reason,
        stats: Some(stats),
    })
}

fn scan_snapshot(config: &RuntimeConfig) -> Result<FreshnessSnapshot> {
    let mut files = Vec::new();
    for root in [config.vault_path.clone(), config.config_path()] {
        if root.is_file() {
            push_tracked(config, &mut files, root)?;
            continue;
        }
        if !root.is_dir() {
            continue;
        }
        for entry in WalkDir::new(root)
            .into_iter()
            .filter_map(std::result::Result::ok)
        {
            let path = entry.path();
            if path.is_file()
                && matches!(
                    path.extension().and_then(|value| value.to_str()),
                    Some("md" | "markdown" | "yaml" | "json")
                )
            {
                push_tracked(config, &mut files, path.to_path_buf())?;
            }
        }
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(FreshnessSnapshot { version: 1, files })
}

fn push_tracked(config: &RuntimeConfig, files: &mut Vec<TrackedFile>, path: PathBuf) -> Result<()> {
    let metadata = fs::metadata(&path)?;
    let modified = metadata.modified()?;
    let mtime_ns = modified
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let display_path = path
        .strip_prefix(&config.home_path)
        .unwrap_or(&path)
        .to_string_lossy()
        .to_string();
    files.push(TrackedFile {
        path: display_path,
        mtime_ns,
        size: metadata.len(),
    });
    Ok(())
}

fn read_snapshot(path: &PathBuf) -> Result<Option<FreshnessSnapshot>> {
    if !path.is_file() {
        return Ok(None);
    }
    Ok(Some(serde_json::from_str(&fs::read_to_string(path)?)?))
}

fn write_snapshot(path: &PathBuf, snapshot: &FreshnessSnapshot) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(snapshot)?)?;
    Ok(())
}

fn state_path(config: &RuntimeConfig) -> PathBuf {
    config
        .state_path()
        .join("cache")
        .join("freshness-state.json")
}
