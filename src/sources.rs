use std::{fs, path::PathBuf};

use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use crate::{
    config::RuntimeConfig,
    error::{MemoraError, Result},
    markdown::{render_markdown, strip_frontmatter},
    util::{content_hash, now_rfc3339, require_file, slugify, unique_path},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceFrontmatter {
    pub schema_version: u16,
    pub source_id: String,
    pub kind: String,
    pub title: String,
    pub captured_at: String,
    pub channel: String,
    pub source_quality: String,
    pub sensitivity: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub risk_flags: Vec<String>,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub origin: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct SourceAddOptions {
    pub path: PathBuf,
    pub extract: Option<PathBuf>,
    pub kind: Option<String>,
    pub format: Option<String>,
    pub title: Option<String>,
    pub url: Option<String>,
    pub sensitivity: Option<String>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct SourceRecord {
    pub source_id: String,
    pub source_path: PathBuf,
    pub extract_path: Option<PathBuf>,
    pub title: String,
}

#[derive(Debug, Clone)]
pub struct SourceSearchResult {
    pub source_id: String,
    pub path: String,
    pub score: usize,
    pub snippet: String,
}

pub fn add_source(config: &RuntimeConfig, options: SourceAddOptions) -> Result<SourceRecord> {
    require_file(&options.path)?;
    if let Some(extract) = &options.extract {
        require_file(extract)?;
    }
    let content = fs::read_to_string(&options.path)?;
    let extract = options
        .extract
        .as_ref()
        .map(fs::read_to_string)
        .transpose()?;
    let title = options.title.unwrap_or_else(|| {
        options
            .path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("source")
            .to_string()
    });
    let source_id = new_source_id(&title);
    let source_dir = unique_path(config.vault_path.join("Sources").join(&source_id));
    let source_id = source_dir
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(&source_id)
        .to_string();
    fs::create_dir_all(&source_dir)?;

    let mut origin = std::collections::BTreeMap::new();
    origin.insert("provider".to_string(), "source_add".to_string());
    origin.insert(
        "format".to_string(),
        options.format.unwrap_or_else(|| "markdown".to_string()),
    );
    origin.insert("content_hash".to_string(), content_hash(&content));

    let frontmatter = SourceFrontmatter {
        schema_version: 1,
        source_id: source_id.clone(),
        kind: "source".to_string(),
        title: title.clone(),
        captured_at: now_rfc3339(),
        channel: options.kind.unwrap_or_else(|| "file".to_string()),
        source_quality: "user_provided".to_string(),
        sensitivity: options.sensitivity.unwrap_or_else(|| "normal".to_string()),
        url: options.url,
        tags: options.tags,
        risk_flags: Vec::new(),
        origin,
    };
    let source_path = source_dir.join("source.md");
    fs::write(
        &source_path,
        render_markdown(&frontmatter, strip_frontmatter(&content))?,
    )?;

    let extract_path = if let Some(extract) = extract {
        let mut extract_frontmatter = frontmatter.clone();
        extract_frontmatter.kind = "extract".to_string();
        let path = source_dir.join("extract.md");
        fs::write(
            &path,
            render_markdown(&extract_frontmatter, strip_frontmatter(&extract))?,
        )?;
        Some(path)
    } else {
        None
    };

    Ok(SourceRecord {
        source_id,
        source_path,
        extract_path,
        title,
    })
}

pub fn lookup_source(config: &RuntimeConfig, source_id: &str, budget: usize) -> Result<String> {
    let source_dir = config.vault_path.join("Sources").join(source_id);
    if !source_dir.is_dir() {
        return Err(MemoraError::NotFound(format!("source {source_id}")));
    }
    let path = if source_dir.join("extract.md").is_file() {
        source_dir.join("extract.md")
    } else {
        source_dir.join("source.md")
    };
    require_file(&path)?;
    let raw = fs::read_to_string(&path)?;
    let text = strip_frontmatter(&raw).trim();
    let selected = if text.len() > budget {
        &text[..budget]
    } else {
        text
    };
    Ok(selected.to_string())
}

pub fn search_sources(
    config: &RuntimeConfig,
    query: &str,
    limit: usize,
) -> Result<Vec<SourceSearchResult>> {
    let root = config.vault_path.join("Sources");
    if !root.exists() {
        return Ok(Vec::new());
    }
    let tokens = tokens(query);
    let mut results = Vec::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_map(std::result::Result::ok)
    {
        let path = entry.path();
        if !path.is_file() || path.extension().and_then(|value| value.to_str()) != Some("md") {
            continue;
        }
        let raw = fs::read_to_string(path)?;
        let text = strip_frontmatter(&raw);
        let haystack = text.to_lowercase();
        let score: usize = tokens
            .iter()
            .map(|token| haystack.matches(token).count())
            .sum();
        if score == 0 && !tokens.is_empty() {
            continue;
        }
        let source_id = path
            .strip_prefix(config.vault_path.join("Sources"))
            .ok()
            .and_then(|relative| relative.components().next())
            .map(|component| component.as_os_str().to_string_lossy().to_string())
            .unwrap_or_else(|| "unknown".to_string());
        let relative_path = path
            .strip_prefix(&config.vault_path)
            .unwrap_or(path)
            .to_string_lossy()
            .to_string();
        results.push(SourceSearchResult {
            source_id,
            path: relative_path,
            score,
            snippet: snippet(text, &tokens),
        });
    }
    results.sort_by(|left, right| {
        right
            .score
            .cmp(&left.score)
            .then_with(|| left.path.cmp(&right.path))
    });
    results.truncate(limit.max(1));
    Ok(results)
}

fn new_source_id(title: &str) -> String {
    let date = now_rfc3339().chars().take(10).collect::<String>();
    format!("{date}_{}", slugify(title))
}

fn tokens(query: &str) -> Vec<String> {
    query
        .split(|ch: char| !ch.is_alphanumeric())
        .filter(|token| !token.trim().is_empty())
        .map(|token| token.to_lowercase())
        .collect()
}

fn snippet(text: &str, tokens: &[String]) -> String {
    let lower = text.to_lowercase();
    let start = tokens
        .iter()
        .find_map(|token| lower.find(token))
        .unwrap_or(0)
        .saturating_sub(80);
    text.chars()
        .skip(start)
        .take(180)
        .collect::<String>()
        .replace('\n', " ")
}
