use std::{fs, path::PathBuf};

use serde::Deserialize;

use crate::{
    config::RuntimeConfig,
    error::{MemoraError, Result},
    memory::{Author, RememberOptions, SourceRef},
    sources::{add_source, SourceAddOptions, SourceRecord},
};

#[derive(Debug, Clone)]
pub struct SessionFinalizeOptions {
    pub transcript: Option<PathBuf>,
    pub summary_file: PathBuf,
    pub memories_file: Option<PathBuf>,
    pub project: Option<String>,
    pub tags: Vec<String>,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct SessionFinalizeResult {
    pub source: Option<SourceRecord>,
    pub proposed_memory_ids: Vec<String>,
    pub dry_run: bool,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum ProposedMemory {
    Text(String),
    Object {
        #[serde(rename = "type", default = "default_memory_type")]
        memory_type: String,
        text: String,
        #[serde(default)]
        tags: Vec<String>,
    },
}

pub fn finalize(
    config: &RuntimeConfig,
    options: SessionFinalizeOptions,
) -> Result<SessionFinalizeResult> {
    if !options.summary_file.is_file() {
        return Err(MemoraError::NotFound(
            options.summary_file.display().to_string(),
        ));
    }
    if let Some(transcript) = &options.transcript {
        if !transcript.is_file() {
            return Err(MemoraError::NotFound(transcript.display().to_string()));
        }
    }
    if options.dry_run {
        return Ok(SessionFinalizeResult {
            source: None,
            proposed_memory_ids: Vec::new(),
            dry_run: true,
        });
    }

    let source = add_source(
        config,
        SourceAddOptions {
            path: options
                .transcript
                .clone()
                .unwrap_or_else(|| options.summary_file.clone()),
            extract: Some(options.summary_file.clone()),
            kind: Some("ai_session".to_string()),
            format: Some("markdown".to_string()),
            title: Some("Session capture".to_string()),
            url: None,
            sensitivity: Some("normal".to_string()),
            tags: options.tags.clone(),
        },
    )?;

    let mut proposed_memory_ids = Vec::new();
    if let Some(memories_file) = options.memories_file {
        if !memories_file.is_file() {
            return Err(MemoraError::NotFound(memories_file.display().to_string()));
        }
        let raw = fs::read_to_string(memories_file)?;
        let proposed: Vec<ProposedMemory> = serde_json::from_str(&raw)?;
        for item in proposed {
            let (memory_type, text, mut tags) = match item {
                ProposedMemory::Text(text) => {
                    ("conversation_summary".to_string(), text, Vec::new())
                }
                ProposedMemory::Object {
                    memory_type,
                    text,
                    tags,
                } => (memory_type, text, tags),
            };
            tags.extend(options.tags.clone());
            let memory = crate::memory::remember(
                config,
                RememberOptions {
                    memory_type,
                    text,
                    scope: options.project.as_ref().map(|_| "project".to_string()),
                    project: options.project.clone(),
                    status: Some("pending".to_string()),
                    tags,
                    source: Some(SourceRef {
                        path: Some(format!("Sources/{}/extract.md", source.source_id)),
                        url: None,
                        title: Some(source.title.clone()),
                    }),
                    author: Some(Author {
                        kind: "agent".to_string(),
                        name: Some("memora session finalize".to_string()),
                    }),
                    confidence: Some(0.65),
                },
            )?;
            proposed_memory_ids.push(memory.frontmatter.id);
        }
    }

    Ok(SessionFinalizeResult {
        source: Some(source),
        proposed_memory_ids,
        dry_run: false,
    })
}

fn default_memory_type() -> String {
    "conversation_summary".to_string()
}
