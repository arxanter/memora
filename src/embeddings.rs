use std::process::Command;

use serde_json::json;

use crate::{
    config::SemanticConfig,
    error::{MemoraError, Result},
    util::content_hash,
};

pub trait EmbeddingProvider {
    fn name(&self) -> &str;
    fn model(&self) -> &str;
    fn embed(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>>;
}

pub fn provider_from_config(config: &SemanticConfig) -> Result<Box<dyn EmbeddingProvider>> {
    let provider = config
        .provider
        .as_deref()
        .ok_or_else(|| MemoraError::InvalidArgument("semantic search is disabled".to_string()))?;
    match provider {
        "deterministic" => Ok(Box::new(DeterministicEmbeddingProvider {
            model: config.model.clone(),
        })),
        "local-command" => Ok(Box::new(LocalCommandEmbeddingProvider {
            model: config.model.clone(),
            command: config.command.clone().ok_or_else(|| {
                MemoraError::InvalidArgument(
                    "semantic provider local-command requires semantic.command".to_string(),
                )
            })?,
        })),
        "fastembed" => Ok(Box::new(FastEmbedEmbeddingProvider::new(&config.model)?)),
        other => Err(MemoraError::InvalidArgument(format!(
            "unsupported semantic provider: {other}"
        ))),
    }
}

pub fn serialize_vector(vector: &[f32]) -> String {
    vector
        .iter()
        .map(|value| value.to_string())
        .collect::<Vec<_>>()
        .join(",")
}

pub fn deserialize_vector(raw: &str) -> Result<Vec<f32>> {
    raw.split(',')
        .map(|value| {
            value.parse::<f32>().map_err(|error| {
                MemoraError::InvalidArgument(format!("invalid stored embedding vector: {error}"))
            })
        })
        .collect()
}

pub fn cosine_similarity(left: &[f32], right: &[f32]) -> f64 {
    if left.len() != right.len() || left.is_empty() {
        return 0.0;
    }
    left.iter()
        .zip(right)
        .map(|(a, b)| f64::from(*a) * f64::from(*b))
        .sum::<f64>()
}

struct DeterministicEmbeddingProvider {
    model: String,
}

impl EmbeddingProvider for DeterministicEmbeddingProvider {
    fn name(&self) -> &str {
        "deterministic"
    }

    fn model(&self) -> &str {
        &self.model
    }

    fn embed(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        Ok(texts.iter().map(|text| embed_deterministic(text)).collect())
    }
}

struct LocalCommandEmbeddingProvider {
    model: String,
    command: Vec<String>,
}

impl EmbeddingProvider for LocalCommandEmbeddingProvider {
    fn name(&self) -> &str {
        "local-command"
    }

    fn model(&self) -> &str {
        &self.model
    }

    fn embed(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        if self.command.is_empty() {
            return Err(MemoraError::InvalidArgument(
                "local-command provider requires a command".to_string(),
            ));
        }
        let payload = json!({
            "model": self.model,
            "texts": texts,
        })
        .to_string();
        let mut command = Command::new(&self.command[0]);
        command.args(&self.command[1..]);
        let output = command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .and_then(|mut child| {
                use std::io::Write;
                child
                    .stdin
                    .as_mut()
                    .expect("stdin piped")
                    .write_all(payload.as_bytes())?;
                child.wait_with_output()
            })?;
        if !output.status.success() {
            return Err(MemoraError::Message(format!(
                "local embedding command failed with status {}",
                output.status
            )));
        }
        let value: serde_json::Value = serde_json::from_slice(&output.stdout)?;
        let embeddings = if let Some(embeddings) = value.get("embeddings") {
            embeddings.clone()
        } else {
            value
        };
        let vectors: Vec<Vec<f32>> = serde_json::from_value(embeddings)?;
        validate_count(vectors, texts.len())
    }
}

struct FastEmbedEmbeddingProvider {
    model: String,
    inner: fastembed::TextEmbedding,
}

impl FastEmbedEmbeddingProvider {
    fn new(model: &str) -> Result<Self> {
        let selected_model = fastembed_model(model)?;
        let inner = fastembed::TextEmbedding::try_new(
            fastembed::InitOptions::new(selected_model).with_show_download_progress(false),
        )
        .map_err(|error| {
            MemoraError::Message(format!("fastembed initialization failed: {error}"))
        })?;
        Ok(Self {
            model: model.to_string(),
            inner,
        })
    }
}

impl EmbeddingProvider for FastEmbedEmbeddingProvider {
    fn name(&self) -> &str {
        "fastembed"
    }

    fn model(&self) -> &str {
        &self.model
    }

    fn embed(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        let documents: Vec<&str> = texts.iter().map(String::as_str).collect();
        let vectors = self.inner.embed(documents, None).map_err(|error| {
            MemoraError::Message(format!("fastembed embedding failed: {error}"))
        })?;
        validate_count(vectors, texts.len())
    }
}

fn fastembed_model(model: &str) -> Result<fastembed::EmbeddingModel> {
    match model {
        "AllMiniLML6V2" | "all-MiniLM-L6-v2" | "sentence-transformers/all-MiniLM-L6-v2" => {
            Ok(fastembed::EmbeddingModel::AllMiniLML6V2)
        }
        other => Err(MemoraError::InvalidArgument(format!(
            "unsupported fastembed model: {other}; supported: AllMiniLML6V2"
        ))),
    }
}

fn validate_count(vectors: Vec<Vec<f32>>, expected: usize) -> Result<Vec<Vec<f32>>> {
    if vectors.len() != expected {
        return Err(MemoraError::Message(format!(
            "embedding provider returned {} vectors for {} texts",
            vectors.len(),
            expected
        )));
    }
    Ok(vectors)
}

fn embed_deterministic(text: &str) -> Vec<f32> {
    const DIMENSIONS: usize = 64;
    let mut vector = vec![0.0; DIMENSIONS];
    for token in vector_tokens(text) {
        let digest = content_hash(&token);
        let index = usize::from_str_radix(&digest[..8], 16).unwrap_or(0) % DIMENSIONS;
        let sign = if u8::from_str_radix(&digest[8..10], 16).unwrap_or(0) % 2 == 0 {
            1.0
        } else {
            -1.0
        };
        vector[index] += sign;
    }
    normalize(&mut vector);
    vector
}

fn vector_tokens(text: &str) -> Vec<String> {
    let base: Vec<String> = text
        .split(|ch: char| !ch.is_alphanumeric())
        .filter(|token| !token.trim().is_empty())
        .map(|token| normalize_token(&token.to_lowercase()))
        .collect();
    let mut tokens = base.clone();
    for window in base.windows(2) {
        tokens.push(format!("{}_{}", window[0], window[1]));
    }
    tokens
}

fn normalize_token(token: &str) -> String {
    if token.len() > 4 && token.ends_with('s') {
        token[..token.len() - 1].to_string()
    } else {
        token.to_string()
    }
}

fn normalize(vector: &mut [f32]) {
    let norm = vector.iter().map(|value| value * value).sum::<f32>().sqrt();
    if norm > 0.0 {
        for value in vector {
            *value /= norm;
        }
    }
}
