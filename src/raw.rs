use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    config::RuntimeConfig,
    error::{MemoraError, Result},
    util::{file_hash, now_rfc3339, slugify, unique_path},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawMetadata {
    pub raw_id: String,
    pub kind: String,
    pub format: String,
    pub title: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub sensitivity: String,
    pub captured_at: String,
    pub original_path: String,
    pub file_name: String,
    pub size_bytes: u64,
    pub content_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_relative_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RawAddOptions {
    pub path: PathBuf,
    pub kind: String,
    pub format: String,
    pub title: Option<String>,
    pub sensitivity: Option<String>,
    pub tags: Vec<String>,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct RawEntry {
    pub path: PathBuf,
    pub relative_path: String,
    pub metadata: Option<RawMetadata>,
}

pub fn add_raw(config: &RuntimeConfig, options: RawAddOptions) -> Result<RawEntry> {
    validate_kind(&options.kind)?;
    validate_format(&options.format)?;
    if !options.path.is_file() {
        return Err(MemoraError::NotFound(options.path.display().to_string()));
    }
    let source_path = options.path.canonicalize()?;
    let file_name = source_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| MemoraError::InvalidArgument("raw file name is invalid".to_string()))?
        .to_string();
    let target_dir = config
        .vault_path
        .join("raw")
        .join("inbox")
        .join(&options.kind);
    let target_path = unique_path(target_dir.join(&file_name));
    let metadata = RawMetadata {
        raw_id: format!("raw_{}", slugify(&file_name)),
        kind: options.kind,
        format: options.format,
        title: options.title.unwrap_or_else(|| file_name.clone()),
        tags: options.tags,
        sensitivity: options.sensitivity.unwrap_or_else(|| "normal".to_string()),
        captured_at: now_rfc3339(),
        original_path: source_path.display().to_string(),
        file_name,
        size_bytes: fs::metadata(&source_path)?.len(),
        content_hash: file_hash(&source_path)?,
        status: None,
        processed_at: None,
        previous_relative_path: None,
        source_id: None,
    };

    if !options.dry_run {
        fs::create_dir_all(
            target_path
                .parent()
                .ok_or_else(|| MemoraError::Message("raw target has no parent".to_string()))?,
        )?;
        fs::copy(&source_path, &target_path)?;
        write_metadata(&target_path, &metadata)?;
    }

    Ok(RawEntry {
        relative_path: relative(config, &target_path),
        path: target_path,
        metadata: Some(metadata),
    })
}

pub fn list_raw(config: &RuntimeConfig, path: Option<PathBuf>) -> Result<Vec<RawEntry>> {
    let root = path.unwrap_or_else(|| config.vault_path.join("raw").join("inbox"));
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut entries = Vec::new();
    for entry in walkdir::WalkDir::new(root)
        .into_iter()
        .filter_map(std::result::Result::ok)
    {
        let path = entry.path();
        if path.is_file() && !is_metadata_path(path) {
            entries.push(RawEntry {
                path: path.to_path_buf(),
                relative_path: relative(config, path),
                metadata: read_metadata(path).ok().flatten(),
            });
        }
    }
    entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    Ok(entries)
}

pub fn inspect_raw(config: &RuntimeConfig, path: PathBuf) -> Result<RawEntry> {
    let resolved = resolve_raw_path(config, path)?;
    Ok(RawEntry {
        relative_path: relative(config, &resolved),
        metadata: read_metadata(&resolved)?,
        path: resolved,
    })
}

pub fn mark_processed(
    config: &RuntimeConfig,
    path: PathBuf,
    source_id: Option<String>,
    dry_run: bool,
) -> Result<RawEntry> {
    let raw_path = resolve_raw_path(config, path)?;
    let previous_relative = relative(config, &raw_path);
    let relative_under_raw = raw_path
        .strip_prefix(config.vault_path.join("raw"))
        .unwrap_or(&raw_path)
        .to_path_buf();
    let processed_tail = if let Ok(tail) = relative_under_raw.strip_prefix("inbox") {
        tail.to_path_buf()
    } else {
        relative_under_raw
    };
    let processed_path = unique_path(
        config
            .vault_path
            .join("raw")
            .join("processed")
            .join(processed_tail),
    );
    let mut metadata = if let Some(metadata) = read_metadata(&raw_path)? {
        metadata
    } else {
        RawMetadata {
            raw_id: format!("raw_{}", slugify(&previous_relative)),
            kind: "text".to_string(),
            format: "txt".to_string(),
            title: raw_path
                .file_stem()
                .and_then(|value| value.to_str())
                .unwrap_or("raw")
                .to_string(),
            tags: Vec::new(),
            sensitivity: "normal".to_string(),
            captured_at: now_rfc3339(),
            original_path: previous_relative.clone(),
            file_name: raw_path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("raw")
                .to_string(),
            size_bytes: fs::metadata(&raw_path)?.len(),
            content_hash: file_hash(&raw_path)?,
            status: None,
            processed_at: None,
            previous_relative_path: None,
            source_id: None,
        }
    };
    metadata.status = Some("processed".to_string());
    metadata.processed_at = Some(now_rfc3339());
    metadata.previous_relative_path = Some(previous_relative);
    metadata.source_id = source_id;
    metadata.content_hash = file_hash(&raw_path)?;

    if !dry_run {
        fs::create_dir_all(processed_path.parent().ok_or_else(|| {
            MemoraError::Message("processed raw target has no parent".to_string())
        })?)?;
        fs::rename(&raw_path, &processed_path)?;
        let old_metadata_path = metadata_path(&raw_path);
        if old_metadata_path.exists() {
            let _ = fs::remove_file(old_metadata_path);
        }
        write_metadata(&processed_path, &metadata)?;
    }

    Ok(RawEntry {
        relative_path: relative(config, &processed_path),
        path: processed_path,
        metadata: Some(metadata),
    })
}

fn validate_kind(kind: &str) -> Result<()> {
    if matches!(
        kind,
        "pdf" | "zoom" | "slack" | "text" | "webclip" | "webclips"
    ) {
        Ok(())
    } else {
        Err(MemoraError::InvalidArgument(format!(
            "unsupported raw kind: {kind}"
        )))
    }
}

fn validate_format(format: &str) -> Result<()> {
    if matches!(format, "pdf" | "markdown" | "json" | "txt") {
        Ok(())
    } else {
        Err(MemoraError::InvalidArgument(format!(
            "unsupported raw format: {format}"
        )))
    }
}

fn resolve_raw_path(config: &RuntimeConfig, path: PathBuf) -> Result<PathBuf> {
    let candidate = if path.is_absolute() {
        path
    } else {
        config.vault_path.join("raw").join(path)
    };
    if candidate.is_file() {
        Ok(candidate)
    } else {
        Err(MemoraError::NotFound(candidate.display().to_string()))
    }
}

fn write_metadata(path: &Path, metadata: &RawMetadata) -> Result<()> {
    let raw = serde_yaml::to_string(metadata)?;
    fs::write(metadata_path(path), raw)?;
    Ok(())
}

fn read_metadata(path: &Path) -> Result<Option<RawMetadata>> {
    let yaml_path = metadata_path(path);
    if yaml_path.is_file() {
        return Ok(Some(serde_yaml::from_str(&fs::read_to_string(yaml_path)?)?));
    }
    let json_path = path.with_file_name(format!(
        "{}.meta.json",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("raw")
    ));
    if json_path.is_file() {
        return Ok(Some(serde_json::from_str(&fs::read_to_string(json_path)?)?));
    }
    Ok(None)
}

fn metadata_path(path: &Path) -> PathBuf {
    path.with_file_name(format!(
        "{}.meta.yaml",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("raw")
    ))
}

fn is_metadata_path(path: &Path) -> bool {
    path.file_name()
        .and_then(|value| value.to_str())
        .map(|name| name.ends_with(".meta.yaml") || name.ends_with(".meta.json"))
        .unwrap_or(false)
}

fn relative(config: &RuntimeConfig, path: &Path) -> String {
    path.strip_prefix(&config.vault_path)
        .unwrap_or(path)
        .to_string_lossy()
        .to_string()
}
