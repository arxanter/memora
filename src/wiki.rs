use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use crate::{
    config::RuntimeConfig,
    error::{MemoraError, Result},
    markdown::{parse_markdown, render_markdown},
    util::{now_rfc3339, slugify},
};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct WikiFrontmatter {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(rename = "type", default, skip_serializing_if = "Option::is_none")]
    pub page_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sources: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub entities: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub concepts: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_updated: Option<String>,
    #[serde(flatten)]
    pub extra: serde_yaml::Mapping,
}

#[derive(Debug, Clone)]
pub struct WikiPage {
    pub path: PathBuf,
    pub relative_path: String,
    pub frontmatter: WikiFrontmatter,
    pub body: String,
}

#[derive(Debug, Clone)]
pub struct WikiSearchResult {
    pub page: WikiPage,
    pub score: usize,
}

pub fn status(config: &RuntimeConfig) -> Result<(usize, Vec<String>)> {
    let pages = list_pages(config)?;
    let issues = lint(config)?;
    Ok((pages.len(), issues))
}

pub fn read_page(config: &RuntimeConfig, target: &str) -> Result<WikiPage> {
    let path = resolve_page(config, target)?;
    read_page_at(config, path)
}

pub fn search(config: &RuntimeConfig, query: &str, limit: usize) -> Result<Vec<WikiSearchResult>> {
    let tokens = tokens(query);
    let mut results = Vec::new();
    for page in list_pages(config)? {
        let haystack = format!(
            "{}\n{}",
            page.frontmatter.title.clone().unwrap_or_default(),
            page.body
        )
        .to_lowercase();
        let score = tokens
            .iter()
            .map(|token| haystack.matches(token).count())
            .sum();
        if score > 0 || tokens.is_empty() {
            results.push(WikiSearchResult { page, score });
        }
    }
    results.sort_by(|left, right| {
        right
            .score
            .cmp(&left.score)
            .then_with(|| left.page.relative_path.cmp(&right.page.relative_path))
    });
    results.truncate(limit);
    Ok(results)
}

pub fn ingest_source(
    config: &RuntimeConfig,
    source_id: &str,
    title: Option<String>,
    entities: Vec<String>,
    concepts: Vec<String>,
) -> Result<Vec<WikiPage>> {
    let source_dir = config.vault_path.join("Sources").join(source_id);
    if !source_dir.is_dir() {
        return Err(MemoraError::NotFound(format!("source {source_id}")));
    }
    let selected_source = if source_dir.join("extract.md").is_file() {
        source_dir.join("extract.md")
    } else {
        source_dir.join("source.md")
    };
    let raw = fs::read_to_string(&selected_source)?;
    let source_text = crate::markdown::strip_frontmatter(&raw);
    let selected_title = title.unwrap_or_else(|| source_id.to_string());
    let source_relative = selected_source
        .strip_prefix(&config.vault_path)
        .unwrap_or(&selected_source)
        .to_string_lossy()
        .to_string();
    let source_page_path = config
        .vault_path
        .join("Wiki")
        .join("sources")
        .join(format!("{}.md", slugify(source_id)));
    let body = format!(
        "# {selected_title}\n\n## Summary\n\n{}\n\n## Source Evidence\n\n- {source_relative}\n",
        first_paragraph(source_text).unwrap_or("No extract summary was available.")
    );
    let page = write_page(
        config,
        source_page_path,
        WikiFrontmatter {
            title: Some(selected_title),
            page_type: Some("source".to_string()),
            source_id: Some(source_id.to_string()),
            sources: vec![source_relative],
            entities: entities.clone(),
            concepts: concepts.clone(),
            last_updated: Some(now_rfc3339()),
            extra: serde_yaml::Mapping::new(),
        },
        body,
    )?;
    let mut written = vec![page];
    for entity in entities {
        written.push(ensure_named_page(config, "entities", &entity, "entity")?);
    }
    for concept in concepts {
        written.push(ensure_named_page(config, "concepts", &concept, "concept")?);
    }
    Ok(written)
}

pub fn synthesize(
    config: &RuntimeConfig,
    question: &str,
    title: Option<String>,
    save: bool,
    limit: usize,
) -> Result<String> {
    let results = search(config, question, limit)?;
    let mut body = format!(
        "# {}\n\n",
        title.clone().unwrap_or_else(|| question.to_string())
    );
    body.push_str("## Question\n\n");
    body.push_str(question);
    body.push_str("\n\n## Candidates\n\n");
    for result in &results {
        body.push_str(&format!(
            "- {} (score {})\n",
            result.page.relative_path, result.score
        ));
    }
    if save {
        let path = config
            .vault_path
            .join("Wiki")
            .join("syntheses")
            .join(format!(
                "{}.md",
                slugify(title.as_deref().unwrap_or(question))
            ));
        write_page(
            config,
            path,
            WikiFrontmatter {
                title,
                page_type: Some("synthesis".to_string()),
                last_updated: Some(now_rfc3339()),
                ..WikiFrontmatter::default()
            },
            body.clone(),
        )?;
    }
    Ok(body)
}

pub fn lint(config: &RuntimeConfig) -> Result<Vec<String>> {
    let mut issues = Vec::new();
    for page in list_pages(config)? {
        let page_type = page.frontmatter.page_type.as_deref().unwrap_or("concept");
        if matches!(page_type, "source" | "entity" | "concept" | "synthesis")
            && page.frontmatter.sources.is_empty()
            && !page.frontmatter.extra.contains_key("memories")
        {
            issues.push(format!(
                "{}: missing sources or memories",
                page.relative_path
            ));
        }
    }
    Ok(issues)
}

pub fn list_pages(config: &RuntimeConfig) -> Result<Vec<WikiPage>> {
    let root = config.vault_path.join("Wiki");
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut pages = Vec::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_map(std::result::Result::ok)
    {
        let path = entry.path();
        if path.is_file()
            && matches!(
                path.extension().and_then(|value| value.to_str()),
                Some("md" | "markdown")
            )
        {
            pages.push(read_page_at(config, path.to_path_buf())?);
        }
    }
    pages.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    Ok(pages)
}

fn read_page_at(config: &RuntimeConfig, path: PathBuf) -> Result<WikiPage> {
    let raw = fs::read_to_string(&path)?;
    let parsed = if raw.starts_with("---\n") {
        parse_markdown::<WikiFrontmatter>(&raw)?
    } else {
        crate::markdown::MarkdownDocument {
            frontmatter: WikiFrontmatter::default(),
            body: raw,
        }
    };
    let relative_path = path
        .strip_prefix(&config.vault_path)
        .unwrap_or(&path)
        .to_string_lossy()
        .to_string();
    Ok(WikiPage {
        path,
        relative_path,
        frontmatter: parsed.frontmatter,
        body: parsed.body,
    })
}

fn write_page(
    config: &RuntimeConfig,
    path: PathBuf,
    frontmatter: WikiFrontmatter,
    body: String,
) -> Result<WikiPage> {
    fs::create_dir_all(
        path.parent()
            .ok_or_else(|| MemoraError::Message("wiki path has no parent".to_string()))?,
    )?;
    fs::write(&path, render_markdown(&frontmatter, &body)?)?;
    read_page_at(config, path)
}

fn ensure_named_page(
    config: &RuntimeConfig,
    directory: &str,
    name: &str,
    page_type: &str,
) -> Result<WikiPage> {
    let path = config
        .vault_path
        .join("Wiki")
        .join(directory)
        .join(format!("{}.md", slugify(name)));
    if path.is_file() {
        return read_page_at(config, path);
    }
    write_page(
        config,
        path,
        WikiFrontmatter {
            title: Some(name.to_string()),
            page_type: Some(page_type.to_string()),
            last_updated: Some(now_rfc3339()),
            ..WikiFrontmatter::default()
        },
        format!("# {name}\n\n## Notes\n\n"),
    )
}

fn resolve_page(config: &RuntimeConfig, target: &str) -> Result<PathBuf> {
    let direct = config.vault_path.join("Wiki").join(target);
    if direct.is_file() {
        return Ok(direct);
    }
    let with_md = config.vault_path.join("Wiki").join(format!("{target}.md"));
    if with_md.is_file() {
        return Ok(with_md);
    }
    for page in list_pages(config)? {
        if page.relative_path == target
            || page.frontmatter.title.as_deref() == Some(target)
            || Path::new(&page.relative_path)
                .file_stem()
                .and_then(|value| value.to_str())
                == Some(target)
        {
            return Ok(page.path);
        }
    }
    Err(MemoraError::NotFound(format!("wiki page {target}")))
}

fn first_paragraph(text: &str) -> Option<&str> {
    text.split("\n\n")
        .map(str::trim)
        .find(|part| !part.is_empty())
}

fn tokens(query: &str) -> Vec<String> {
    query
        .split(|ch: char| !ch.is_alphanumeric())
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_lowercase)
        .collect()
}
