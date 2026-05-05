use std::collections::HashMap;

use rusqlite::{params, Connection, OptionalExtension};

use crate::{
    config::RuntimeConfig,
    embeddings::{
        cosine_similarity, deserialize_vector, provider_from_config, serialize_vector,
        EmbeddingProvider,
    },
    error::{MemoraError, Result},
    memory::{list_memories, MemoryDocument},
    util::content_hash,
};

#[derive(Debug, Clone)]
pub struct ReindexStats {
    pub documents_seen: usize,
    pub documents_indexed: usize,
    pub documents_skipped: usize,
    pub documents_removed: usize,
    pub chunks_indexed: usize,
}

#[derive(Debug, Clone)]
pub struct SearchFilters {
    pub project: Option<String>,
    pub memory_type: Option<String>,
    pub status: Option<String>,
    pub scope: Option<String>,
    pub limit: usize,
    pub mode: SearchMode,
}

#[derive(Debug, Clone)]
pub struct SearchResult {
    pub id: String,
    pub path: String,
    pub memory_type: String,
    pub status: String,
    pub score: f64,
    pub snippet: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SearchMode {
    Auto,
    Text,
    Vector,
    Hybrid,
}

impl SearchMode {
    pub fn parse(value: &str) -> Result<Self> {
        match value.trim().to_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "text" => Ok(Self::Text),
            "vector" => Ok(Self::Vector),
            "hybrid" => Ok(Self::Hybrid),
            other => Err(MemoraError::InvalidArgument(format!(
                "unsupported search mode: {other}"
            ))),
        }
    }
}

pub fn reindex(config: &RuntimeConfig, clean: bool) -> Result<ReindexStats> {
    std::fs::create_dir_all(config.state_path())?;
    if clean && config.index_path().exists() {
        std::fs::remove_file(config.index_path())?;
    }

    let memories = list_memories(config)?;
    let connection = Connection::open(config.index_path())?;
    ensure_schema(&connection)?;

    let mut documents_indexed = 0;
    let mut documents_skipped = 0;
    let mut chunks_indexed = 0;
    let mut seen_ids = Vec::new();

    for memory in &memories {
        seen_ids.push(memory.frontmatter.id.clone());
        let document_hash = content_hash(&format!(
            "{}\n{}",
            serde_yaml::to_string(&memory.frontmatter)?,
            memory.body
        ));
        let existing_hash: Option<String> = connection
            .query_row(
                "SELECT content_hash FROM documents WHERE id = ?1",
                params![memory.frontmatter.id],
                |row| row.get(0),
            )
            .optional()?;

        upsert_document(&connection, memory, &document_hash)?;
        if existing_hash.as_deref() == Some(document_hash.as_str()) {
            documents_skipped += 1;
            continue;
        }

        replace_chunks(&connection, memory)?;
        replace_links(&connection, memory)?;
        chunks_indexed += 1;
        documents_indexed += 1;
    }

    let removed = remove_stale_documents(&connection, &seen_ids)?;
    Ok(ReindexStats {
        documents_seen: memories.len(),
        documents_indexed,
        documents_skipped,
        documents_removed: removed,
        chunks_indexed,
    })
}

pub fn search(
    config: &RuntimeConfig,
    query: &str,
    filters: SearchFilters,
) -> Result<Vec<SearchResult>> {
    if !config.index_path().is_file() {
        return Err(MemoraError::NotFound(format!(
            "index {}; run `memora reindex`",
            config.index_path().display()
        )));
    }
    let connection = Connection::open(config.index_path())?;
    ensure_schema(&connection)?;
    let effective_mode = filters.mode;
    if effective_mode == SearchMode::Text {
        return text_search(&connection, query, &filters);
    }
    let provider = match provider_from_config(&config.file.semantic) {
        Ok(provider) => provider,
        Err(error) if effective_mode == SearchMode::Auto => {
            eprintln!("semantic disabled; falling back to text search: {error}");
            return text_search(&connection, query, &filters);
        }
        Err(error) => return Err(error),
    };
    if effective_mode == SearchMode::Vector {
        return vector_search(&connection, query, &filters, provider);
    }

    let mut merged: HashMap<String, SearchResult> = HashMap::new();
    for result in text_search(&connection, query, &filters).unwrap_or_default() {
        merged.insert(result.id.clone(), result);
    }
    for mut result in vector_search(&connection, query, &filters, provider).unwrap_or_default() {
        if let Some(existing) = merged.get_mut(&result.id) {
            existing.score += result.score * 0.8;
            if existing.snippet.trim().is_empty() {
                existing.snippet = result.snippet;
            }
        } else {
            result.score *= 0.8;
            merged.insert(result.id.clone(), result);
        }
    }
    let mut results: Vec<_> = merged.into_values().collect();
    results.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.id.cmp(&right.id))
    });
    results.truncate(filters.limit.max(1));
    Ok(results)
}

fn text_search(
    connection: &Connection,
    query: &str,
    filters: &SearchFilters,
) -> Result<Vec<SearchResult>> {
    let fts_query = fts_query(query);
    let limit = filters.limit.max(1) as i64;
    let mut sql = String::from(
        r#"
        SELECT d.id, d.path, m.type, m.status, bm25(chunk_fts) AS rank,
               snippet(chunk_fts, 3, '[', ']', '...', 12) AS snippet
        FROM chunk_fts
        JOIN documents d ON d.id = chunk_fts.document_id
        JOIN memories m ON m.id = d.id
        WHERE chunk_fts MATCH ?1
        "#,
    );
    let mut filter_values = Vec::new();
    if let Some(project) = &filters.project {
        sql.push_str(" AND m.project = ?");
        filter_values.push(project.clone());
    }
    if let Some(memory_type) = &filters.memory_type {
        sql.push_str(" AND m.type = ?");
        filter_values.push(memory_type.clone());
    }
    if let Some(status) = &filters.status {
        sql.push_str(" AND m.status = ?");
        filter_values.push(status.clone());
    } else {
        sql.push_str(" AND m.status IN ('active', 'pending', 'stale')");
    }
    if let Some(scope) = &filters.scope {
        sql.push_str(" AND m.scope = ?");
        filter_values.push(scope.clone());
    }
    sql.push_str(" ORDER BY rank ASC, d.id ASC LIMIT ?");

    let mut values: Vec<&dyn rusqlite::ToSql> = vec![&fts_query];
    for value in &filter_values {
        values.push(value);
    }
    values.push(&limit);

    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(values.as_slice(), |row| {
        let rank: f64 = row.get(4)?;
        Ok(SearchResult {
            id: row.get(0)?,
            path: row.get(1)?,
            memory_type: row.get(2)?,
            status: row.get(3)?,
            score: 10.0 / (1.0 + rank.abs()),
            snippet: row.get(5)?,
        })
    })?;

    let mut results = Vec::new();
    for row in rows {
        results.push(row?);
    }
    Ok(results)
}

fn ensure_schema(connection: &Connection) -> Result<()> {
    connection.execute_batch(
        r#"
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_type TEXT NOT NULL,
            text TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            scope TEXT NOT NULL,
            project TEXT,
            status TEXT NOT NULL,
            confidence REAL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS links (
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL,
            PRIMARY KEY (from_id, to_id, relation)
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id TEXT NOT NULL,
            model TEXT NOT NULL,
            vector TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (chunk_id, model),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
            id UNINDEXED,
            document_id UNINDEXED,
            chunk_type UNINDEXED,
            text,
            content_hash UNINDEXED
        );
        "#,
    )?;
    Ok(())
}

fn upsert_document(connection: &Connection, memory: &MemoryDocument, hash: &str) -> Result<()> {
    connection.execute(
        r#"
        INSERT INTO documents (id, path, type, status, created_at, updated_at, content_hash)
        VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
        ON CONFLICT(id) DO UPDATE SET
            path = excluded.path,
            type = excluded.type,
            status = excluded.status,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            content_hash = excluded.content_hash
        "#,
        params![
            memory.frontmatter.id,
            memory.relative_path,
            memory.frontmatter.memory_type,
            memory.frontmatter.status,
            memory.frontmatter.created_at,
            memory.frontmatter.updated_at,
            hash
        ],
    )?;
    connection.execute(
        r#"
        INSERT INTO memories (id, document_id, type, scope, project, status, confidence)
        VALUES (?1, ?1, ?2, ?3, ?4, ?5, ?6)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            scope = excluded.scope,
            project = excluded.project,
            status = excluded.status,
            confidence = excluded.confidence
        "#,
        params![
            memory.frontmatter.id,
            memory.frontmatter.memory_type,
            memory.frontmatter.scope,
            memory.frontmatter.project,
            memory.frontmatter.status,
            memory.frontmatter.confidence
        ],
    )?;
    Ok(())
}

fn replace_chunks(connection: &Connection, memory: &MemoryDocument) -> Result<()> {
    connection.execute(
        "DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?1)",
        params![memory.frontmatter.id],
    )?;
    connection.execute(
        "DELETE FROM chunk_fts WHERE document_id = ?1",
        params![memory.frontmatter.id],
    )?;
    connection.execute(
        "DELETE FROM chunks WHERE document_id = ?1",
        params![memory.frontmatter.id],
    )?;

    let text = memory.body.trim();
    if text.is_empty() {
        return Ok(());
    }
    let hash = content_hash(text);
    let chunk_id = format!("{}:chunk:1:{}", memory.frontmatter.id, &hash[..12]);
    connection.execute(
        "INSERT INTO chunks (id, document_id, chunk_type, text, token_estimate, content_hash) VALUES (?1, ?2, 'body', ?3, ?4, ?5)",
        params![
            chunk_id,
            memory.frontmatter.id,
            text,
            estimate_tokens(text),
            hash
        ],
    )?;
    connection.execute(
        "INSERT INTO chunk_fts (id, document_id, chunk_type, text, content_hash) VALUES (?1, ?2, 'body', ?3, ?4)",
        params![chunk_id, memory.frontmatter.id, text, hash],
    )?;
    Ok(())
}

fn replace_links(connection: &Connection, memory: &MemoryDocument) -> Result<()> {
    connection.execute(
        "DELETE FROM links WHERE from_id = ?1",
        params![memory.frontmatter.id],
    )?;
    for relation in &memory.frontmatter.relations {
        connection.execute(
            "INSERT OR REPLACE INTO links (from_id, to_id, relation, confidence) VALUES (?1, ?2, ?3, ?4)",
            params![
                memory.frontmatter.id,
                relation.target,
                relation.relation_type,
                relation.confidence
            ],
        )?;
    }
    Ok(())
}

fn remove_stale_documents(connection: &Connection, seen_ids: &[String]) -> Result<usize> {
    let mut statement = connection.prepare("SELECT id FROM documents")?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    let seen: std::collections::HashSet<_> = seen_ids.iter().cloned().collect();
    let mut removed = 0;
    for row in rows {
        let id = row?;
        if !seen.contains(&id) {
            connection.execute("DELETE FROM chunk_fts WHERE document_id = ?1", params![id])?;
            connection.execute("DELETE FROM documents WHERE id = ?1", params![id])?;
            removed += 1;
        }
    }
    Ok(removed)
}

fn fts_query(query: &str) -> String {
    let tokens: Vec<String> = query
        .split(|ch: char| !ch.is_alphanumeric() && ch != '_')
        .filter(|token| !token.trim().is_empty())
        .map(|token| format!("\"{}\"", token.replace('"', "\"\"")))
        .collect();
    if tokens.is_empty() {
        "\"\"".to_string()
    } else {
        tokens.join(" OR ")
    }
}

fn estimate_tokens(text: &str) -> i64 {
    std::cmp::max(1, (text.split_whitespace().count() as f64 / 0.75) as i64)
}

fn vector_search(
    connection: &Connection,
    query: &str,
    filters: &SearchFilters,
    mut provider: Box<dyn EmbeddingProvider>,
) -> Result<Vec<SearchResult>> {
    let query_vector = provider.embed(&[format!("query: {query}")])?.remove(0);
    let mut sql = String::from(
        r#"
        SELECT c.id, c.text, c.content_hash, d.id, d.path, m.type, m.status
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN memories m ON m.id = d.id
        WHERE 1 = 1
        "#,
    );
    let mut filter_values = Vec::new();
    if let Some(project) = &filters.project {
        sql.push_str(" AND m.project = ?");
        filter_values.push(project.clone());
    }
    if let Some(memory_type) = &filters.memory_type {
        sql.push_str(" AND m.type = ?");
        filter_values.push(memory_type.clone());
    }
    if let Some(status) = &filters.status {
        sql.push_str(" AND m.status = ?");
        filter_values.push(status.clone());
    } else {
        sql.push_str(" AND m.status IN ('active', 'pending', 'stale')");
    }
    if let Some(scope) = &filters.scope {
        sql.push_str(" AND m.scope = ?");
        filter_values.push(scope.clone());
    }
    sql.push_str(" LIMIT ?");

    let fetch_limit = (filters.limit.max(1) * 20).max(100) as i64;
    let mut values: Vec<&dyn rusqlite::ToSql> = Vec::new();
    for value in &filter_values {
        values.push(value);
    }
    values.push(&fetch_limit);

    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(values.as_slice(), |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, String>(4)?,
            row.get::<_, String>(5)?,
            row.get::<_, String>(6)?,
        ))
    })?;

    let mut results = Vec::new();
    for row in rows {
        let (chunk_id, text, chunk_hash, id, path, memory_type, status) = row?;
        let vector =
            embedding_for_chunk(connection, &mut *provider, &chunk_id, &text, &chunk_hash)?;
        let similarity = cosine_similarity(&query_vector, &vector);
        if similarity <= 0.05 {
            continue;
        }
        results.push(SearchResult {
            id,
            path,
            memory_type,
            status,
            score: 10.0 * similarity,
            snippet: vector_snippet(&text, query),
        });
    }
    results.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.id.cmp(&right.id))
    });
    results.truncate(filters.limit.max(1));
    Ok(results)
}

fn embedding_for_chunk(
    connection: &Connection,
    provider: &mut dyn EmbeddingProvider,
    chunk_id: &str,
    text: &str,
    content_hash: &str,
) -> Result<Vec<f32>> {
    let model = format!("{}:{}", provider.name(), provider.model());
    let existing: Option<(String, String)> = connection
        .query_row(
            "SELECT vector, content_hash FROM embeddings WHERE chunk_id = ?1 AND model = ?2",
            params![chunk_id, model],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    if let Some((vector, hash)) = existing {
        if hash == content_hash {
            return deserialize_vector(&vector);
        }
    }

    let vector = provider.embed(&[format!("passage: {text}")])?.remove(0);
    let serialized = serialize_vector(&vector);
    connection.execute(
        "INSERT OR REPLACE INTO embeddings (chunk_id, model, vector, content_hash) VALUES (?1, ?2, ?3, ?4)",
        params![chunk_id, model, serialized, content_hash],
    )?;
    Ok(vector)
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

fn vector_snippet(text: &str, query: &str) -> String {
    let query_tokens = vector_tokens(query);
    let lower = text.to_lowercase();
    for token in query_tokens {
        if let Some(index) = lower.find(&token.replace('_', " ")) {
            let start = index.saturating_sub(80);
            let end = std::cmp::min(text.len(), index + 160);
            return text[start..end].replace('\n', " ");
        }
    }
    text.chars().take(180).collect()
}
