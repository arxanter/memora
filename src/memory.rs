use std::{fs, path::PathBuf};

use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use crate::{
    config::RuntimeConfig,
    error::{MemoraError, Result},
    markdown::{parse_markdown, render_markdown},
    util::{now_rfc3339, short_unique_suffix, slugify},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceRef {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Author {
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relation {
    #[serde(rename = "type")]
    pub relation_type: String,
    pub target: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryFrontmatter {
    pub schema_version: u16,
    pub id: String,
    #[serde(rename = "type")]
    pub memory_type: String,
    #[serde(default = "default_scope")]
    pub scope: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project: Option<String>,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f32>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<SourceRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub author: Option<Author>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub relations: Vec<Relation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    #[serde(flatten)]
    pub extra: serde_yaml::Mapping,
}

#[derive(Debug, Clone)]
pub struct MemoryDocument {
    pub path: PathBuf,
    pub relative_path: String,
    pub frontmatter: MemoryFrontmatter,
    pub body: String,
}

#[derive(Debug, Clone)]
pub struct RememberOptions {
    pub memory_type: String,
    pub text: String,
    pub scope: Option<String>,
    pub project: Option<String>,
    pub status: Option<String>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct MemoryUpdateOptions {
    pub memory_id: String,
    pub memory_type: Option<String>,
    pub scope: Option<String>,
    pub project: Option<String>,
    pub clear_project: bool,
    pub status: Option<String>,
    pub confidence: Option<f32>,
    pub clear_confidence: bool,
    pub tags: Vec<String>,
    pub clear_tags: bool,
    pub text: Option<String>,
}

pub fn remember(config: &RuntimeConfig, options: RememberOptions) -> Result<MemoryDocument> {
    validate_memory_type(&options.memory_type)?;
    let scope = options.scope.unwrap_or_else(|| {
        if options.project.is_some() {
            "project"
        } else {
            "user"
        }
        .to_string()
    });
    validate_scope(&scope)?;
    if scope == "project" && options.project.as_deref().unwrap_or("").trim().is_empty() {
        return Err(MemoraError::InvalidArgument(
            "project-scoped memory must include --project".to_string(),
        ));
    }

    let now = now_rfc3339();
    let id = new_memory_id(&options.memory_type, &options.text);
    let frontmatter = MemoryFrontmatter {
        schema_version: 1,
        id: id.clone(),
        memory_type: options.memory_type.clone(),
        scope,
        project: options.project,
        status: options.status.unwrap_or_else(|| "pending".to_string()),
        confidence: None,
        created_at: now.clone(),
        updated_at: now,
        source: None,
        author: None,
        relations: Vec::new(),
        tags: options.tags,
        extra: serde_yaml::Mapping::new(),
    };
    validate_frontmatter(&frontmatter)?;

    let directory = config
        .vault_path
        .join("Memories")
        .join(memory_type_directory(&frontmatter.memory_type));
    fs::create_dir_all(&directory)?;
    let path = directory.join(format!("{}.md", slugify(&id)));
    let rendered = render_markdown(&frontmatter, &options.text)?;
    fs::write(&path, rendered)?;
    read_memory_at(config, path)
}

pub fn inspect(config: &RuntimeConfig, memory_id: &str) -> Result<MemoryDocument> {
    find_memory(config, memory_id)?
        .ok_or_else(|| MemoraError::NotFound(format!("memory {memory_id}")))
}

pub fn update_memory(
    config: &RuntimeConfig,
    options: MemoryUpdateOptions,
) -> Result<MemoryDocument> {
    let mut document = inspect(config, &options.memory_id)?;
    if let Some(memory_type) = options.memory_type {
        validate_memory_type(&memory_type)?;
        document.frontmatter.memory_type = memory_type;
    }
    if let Some(scope) = options.scope {
        validate_scope(&scope)?;
        document.frontmatter.scope = scope;
    }
    if options.clear_project {
        document.frontmatter.project = None;
    }
    if let Some(project) = options.project {
        document.frontmatter.project = Some(project);
    }
    if let Some(status) = options.status {
        validate_status(&status)?;
        document.frontmatter.status = status;
    }
    if options.clear_confidence {
        document.frontmatter.confidence = None;
    }
    if let Some(confidence) = options.confidence {
        if !(0.0..=1.0).contains(&confidence) {
            return Err(MemoraError::InvalidArgument(
                "confidence must be between 0 and 1".to_string(),
            ));
        }
        document.frontmatter.confidence = Some(confidence);
    }
    if options.clear_tags {
        document.frontmatter.tags.clear();
    }
    if !options.tags.is_empty() {
        document.frontmatter.tags = options.tags;
    }
    if let Some(text) = options.text {
        document.body = text;
    }
    document.frontmatter.updated_at = now_rfc3339();
    validate_frontmatter(&document.frontmatter)?;

    let expected_dir = config
        .vault_path
        .join("Memories")
        .join(memory_type_directory(&document.frontmatter.memory_type));
    fs::create_dir_all(&expected_dir)?;
    let next_path = expected_dir.join(
        document
            .path
            .file_name()
            .ok_or_else(|| MemoraError::Message("memory path has no file name".to_string()))?,
    );
    let rendered = render_markdown(&document.frontmatter, &document.body)?;
    if next_path != document.path {
        fs::remove_file(&document.path)?;
    }
    fs::write(&next_path, rendered)?;
    read_memory_at(config, next_path)
}

pub fn list_memories(config: &RuntimeConfig) -> Result<Vec<MemoryDocument>> {
    let root = config.vault_path.join("Memories");
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut memories = Vec::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_map(std::result::Result::ok)
    {
        let path = entry.path();
        if path.is_file() && path.extension().and_then(|ext| ext.to_str()) == Some("md") {
            memories.push(read_memory_at(config, path.to_path_buf())?);
        }
    }
    memories.sort_by(|left, right| left.frontmatter.id.cmp(&right.frontmatter.id));
    Ok(memories)
}

pub fn pending_memories(config: &RuntimeConfig) -> Result<Vec<MemoryDocument>> {
    Ok(list_memories(config)?
        .into_iter()
        .filter(|memory| memory.frontmatter.status == "pending")
        .collect())
}

pub fn set_review_status(
    config: &RuntimeConfig,
    ids: &[String],
    status: &str,
) -> Result<Vec<MemoryDocument>> {
    let mut updated = Vec::new();
    for id in ids {
        updated.push(update_memory(
            config,
            MemoryUpdateOptions {
                memory_id: id.clone(),
                memory_type: None,
                scope: None,
                project: None,
                clear_project: false,
                status: Some(status.to_string()),
                confidence: None,
                clear_confidence: false,
                tags: Vec::new(),
                clear_tags: false,
                text: None,
            },
        )?);
    }
    Ok(updated)
}

pub fn validate_all(config: &RuntimeConfig) -> Result<Vec<String>> {
    let mut issues = Vec::new();
    let memories = list_memories(config)?;
    let ids: std::collections::HashSet<_> = memories
        .iter()
        .map(|memory| memory.frontmatter.id.as_str())
        .collect();
    for memory in &memories {
        if let Err(error) = validate_frontmatter(&memory.frontmatter) {
            issues.push(format!("{}: {error}", memory.relative_path));
        }
        for relation in &memory.frontmatter.relations {
            if !ids.contains(relation.target.as_str()) {
                issues.push(format!(
                    "{}: relation target not found: {}",
                    memory.relative_path, relation.target
                ));
            }
        }
    }
    Ok(issues)
}

fn find_memory(config: &RuntimeConfig, memory_id: &str) -> Result<Option<MemoryDocument>> {
    for memory in list_memories(config)? {
        if memory.frontmatter.id == memory_id {
            return Ok(Some(memory));
        }
    }
    Ok(None)
}

fn read_memory_at(config: &RuntimeConfig, path: PathBuf) -> Result<MemoryDocument> {
    let raw = fs::read_to_string(&path)?;
    let parsed = parse_markdown::<MemoryFrontmatter>(&raw)?;
    validate_frontmatter(&parsed.frontmatter)?;
    let relative_path = path
        .strip_prefix(&config.vault_path)
        .unwrap_or(&path)
        .to_string_lossy()
        .to_string();
    Ok(MemoryDocument {
        path,
        relative_path,
        frontmatter: parsed.frontmatter,
        body: parsed.body,
    })
}

fn validate_frontmatter(frontmatter: &MemoryFrontmatter) -> Result<()> {
    if frontmatter.schema_version != 1 {
        return Err(MemoraError::InvalidArgument(
            "memory schema_version must be 1".to_string(),
        ));
    }
    validate_memory_type(&frontmatter.memory_type)?;
    validate_status(&frontmatter.status)?;
    validate_scope(&frontmatter.scope)?;
    if frontmatter.scope == "project" && frontmatter.project.as_deref().unwrap_or("").is_empty() {
        return Err(MemoraError::InvalidArgument(
            "project-scoped memory must include project".to_string(),
        ));
    }
    Ok(())
}

fn validate_memory_type(memory_type: &str) -> Result<()> {
    if matches!(
        memory_type,
        "fact" | "decision" | "preference" | "task" | "project_context" | "conversation_summary"
    ) {
        Ok(())
    } else {
        Err(MemoraError::InvalidArgument(format!(
            "unsupported memory type: {memory_type}"
        )))
    }
}

fn validate_status(status: &str) -> Result<()> {
    if matches!(
        status,
        "pending" | "active" | "stale" | "superseded" | "rejected"
    ) {
        Ok(())
    } else {
        Err(MemoraError::InvalidArgument(format!(
            "unsupported memory status: {status}"
        )))
    }
}

fn validate_scope(scope: &str) -> Result<()> {
    if matches!(scope, "user" | "project" | "global") {
        Ok(())
    } else {
        Err(MemoraError::InvalidArgument(format!(
            "unsupported memory scope: {scope}"
        )))
    }
}

fn memory_type_directory(memory_type: &str) -> &'static str {
    match memory_type {
        "fact" => "facts",
        "decision" => "decisions",
        "preference" => "preferences",
        "task" => "tasks",
        "project_context" => "context",
        "conversation_summary" => "conversations",
        _ => "facts",
    }
}

fn new_memory_id(memory_type: &str, text: &str) -> String {
    format!(
        "mem_{}_{}",
        memory_type,
        short_unique_suffix(&format!("{memory_type}:{text}"))
    )
}

fn default_scope() -> String {
    "user".to_string()
}
