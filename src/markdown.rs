use serde::{de::DeserializeOwned, Serialize};

use crate::error::{MemoraError, Result};

const FRONTMATTER_BOUNDARY: &str = "---";

#[derive(Debug, Clone)]
pub struct MarkdownDocument<T> {
    pub frontmatter: T,
    pub body: String,
}

pub fn parse_markdown<T>(raw: &str) -> Result<MarkdownDocument<T>>
where
    T: DeserializeOwned,
{
    let Some(rest) = raw.strip_prefix("---\n") else {
        return Err(MemoraError::InvalidArgument(
            "markdown document must start with YAML frontmatter".to_string(),
        ));
    };
    let Some((yaml, body)) = rest.split_once("\n---") else {
        return Err(MemoraError::InvalidArgument(
            "markdown document is missing closing frontmatter boundary".to_string(),
        ));
    };
    let body = body.strip_prefix('\n').unwrap_or(body);
    Ok(MarkdownDocument {
        frontmatter: serde_yaml::from_str(yaml)?,
        body: body.to_string(),
    })
}

pub fn render_markdown<T>(frontmatter: &T, body: &str) -> Result<String>
where
    T: Serialize,
{
    let yaml = serde_yaml::to_string(frontmatter)?;
    let mut rendered = String::new();
    rendered.push_str(FRONTMATTER_BOUNDARY);
    rendered.push('\n');
    rendered.push_str(yaml.trim_end());
    rendered.push('\n');
    rendered.push_str(FRONTMATTER_BOUNDARY);
    rendered.push_str("\n\n");
    rendered.push_str(body.trim());
    rendered.push('\n');
    Ok(rendered)
}

pub fn strip_frontmatter(raw: &str) -> &str {
    if let Some(rest) = raw.strip_prefix("---\n") {
        if let Some((_, body)) = rest.split_once("\n---") {
            return body.strip_prefix('\n').unwrap_or(body);
        }
    }
    raw
}
