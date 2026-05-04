"""Markdown-to-SQLite indexer for Memora vaults."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

import yaml
from pydantic import ValidationError

from config import MemoryConfig
from schema import (
    MemoryDocument,
    RelationType,
    ValidationIssue,
    iter_memory_markdown_files,
    parse_markdown_document,
    validate_vault,
)
from sync import vault_lock

PathLike = Union[Path, str]


@dataclass(frozen=True)
class IndexedChunk:
    id: str
    document_id: str
    chunk_type: str
    text: str
    token_estimate: int
    content_hash: str


@dataclass(frozen=True)
class GraphIssue:
    path: Path
    from_id: str
    to_id: str
    relation: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation": self.relation,
            "message": self.message,
        }


@dataclass(frozen=True)
class GraphValidationReport:
    documents: int
    links: int
    issues: tuple[GraphIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def orphan_count(self) -> int:
        return len(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "documents": self.documents,
            "links": self.links,
            "orphan_count": self.orphan_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ReindexResult:
    index_path: Path
    documents_seen: int
    documents_indexed: int
    documents_skipped: int
    documents_removed: int
    chunks_indexed: int
    chunks_skipped: int
    observations_indexed: int
    links_indexed: int
    graph: GraphValidationReport

    @property
    def ok(self) -> bool:
        return self.graph.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "index_path": str(self.index_path),
            "documents_seen": self.documents_seen,
            "documents_indexed": self.documents_indexed,
            "documents_skipped": self.documents_skipped,
            "documents_removed": self.documents_removed,
            "chunks_indexed": self.chunks_indexed,
            "chunks_skipped": self.chunks_skipped,
            "observations_indexed": self.observations_indexed,
            "links_indexed": self.links_indexed,
            "graph_ok": self.graph.ok,
            "orphan_count": self.graph.orphan_count,
            "graph_issues": [issue.to_dict() for issue in self.graph.issues],
        }


@dataclass(frozen=True)
class KeywordSearchResult:
    chunk_id: str
    document_id: str
    chunk_type: str
    text: str


def reindex_vault(config: MemoryConfig, *, clean: bool = False) -> ReindexResult:
    """Rebuild or incrementally refresh the disposable SQLite index."""

    with vault_lock(config):
        return _reindex_vault_unlocked(config, clean=clean)


def _reindex_vault_unlocked(config: MemoryConfig, *, clean: bool = False) -> ReindexResult:
    """Rebuild or incrementally refresh the disposable SQLite index without taking a lock."""

    config.index_file.parent.mkdir(parents=True, exist_ok=True)
    if clean and config.index_file.exists():
        config.index_file.unlink()

    parsed = _read_valid_memory_documents(config)
    graph = validate_graph_documents(parsed)

    with sqlite3.connect(config.index_file) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_schema(connection)

        seen_ids: set[str] = set()
        documents_indexed = 0
        documents_skipped = 0
        chunks_indexed = 0
        chunks_skipped = 0
        observations_indexed = 0
        links_indexed = 0

        for document, raw_markdown, relative_path in parsed:
            frontmatter = document.frontmatter
            document_id = frontmatter.id
            seen_ids.add(document_id)
            document_hash = content_hash(raw_markdown)

            existing_hash = _document_hash(connection, document_id)
            if existing_hash == document_hash:
                _upsert_document(connection, document, relative_path, document_hash)
                documents_skipped += 1
                chunks_skipped += _chunk_count(connection, document_id)
                continue

            _delete_conflicting_path(connection, document_id, relative_path)
            _replace_document_index(connection, document, relative_path, document_hash)
            chunks = split_document_chunks(document)
            _replace_chunks(connection, document_id, chunks)
            observations_indexed += _replace_observations(connection, document, document_hash)
            links_indexed += _replace_links(connection, document)
            documents_indexed += 1
            chunks_indexed += len(chunks)

        stale_ids = _stale_document_ids(connection, seen_ids)
        for document_id in stale_ids:
            _delete_document_index(connection, document_id)

    return ReindexResult(
        index_path=config.index_file,
        documents_seen=len(parsed),
        documents_indexed=documents_indexed,
        documents_skipped=documents_skipped,
        documents_removed=len(stale_ids),
        chunks_indexed=chunks_indexed,
        chunks_skipped=chunks_skipped,
        observations_indexed=observations_indexed,
        links_indexed=links_indexed,
        graph=graph,
    )


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create Stage 4 SQLite tables and FTS5 index if they are missing."""

    connection.executescript(
        """
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
        CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);

        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id TEXT NOT NULL,
            model TEXT NOT NULL,
            vector TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chunk_id, model),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_model_hash ON embeddings(model, content_hash);

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            scope TEXT NOT NULL,
            project TEXT,
            status TEXT NOT NULL,
            confidence REAL,
            valid_from TEXT,
            valid_to TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memories_type_status ON memories(type, status);
        CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);

        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            confidence REAL,
            content_hash TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_observations_document_id ON observations(document_id);
        CREATE INDEX IF NOT EXISTS idx_observations_content_hash ON observations(content_hash);

        CREATE TABLE IF NOT EXISTS links (
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL,
            PRIMARY KEY (from_id, to_id, relation),
            FOREIGN KEY (from_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_links_to_id ON links(to_id);
        CREATE INDEX IF NOT EXISTS idx_links_relation ON links(relation);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
            id UNINDEXED,
            document_id UNINDEXED,
            chunk_type UNINDEXED,
            text,
            content_hash UNINDEXED
        );
        """
    )


def validate_graph(config: MemoryConfig) -> GraphValidationReport:
    """Validate durable memora graph references in a vault."""

    report = validate_vault(config.vault_path)
    issues = tuple(
        GraphIssue(
            path=issue.path,
            from_id="",
            to_id="",
            relation="schema",
            message=issue.message,
        )
        for issue in report.issues
    )
    if issues:
        return GraphValidationReport(documents=len(report.documents), links=0, issues=issues)
    return validate_graph_documents(
        tuple(
            (
                document,
                "",
                document.path.relative_to(config.vault_path).as_posix()
                if document.path and document.path.is_absolute()
                else str(document.path or ""),
            )
            for document in report.documents
        )
    )


def validate_graph_documents(
    documents: Sequence[tuple[MemoryDocument, str, str]],
) -> GraphValidationReport:
    """Return orphan relation issues for already parsed memory documents."""

    known_ids = {document.frontmatter.id for document, _, _ in documents}
    issues: list[GraphIssue] = []
    link_count = 0

    for document, _, relative_path in documents:
        frontmatter = document.frontmatter
        path = Path(relative_path)
        for relation, target, confidence in _iter_relation_edges(frontmatter):
            del confidence
            link_count += 1
            if target not in known_ids:
                issues.append(
                    GraphIssue(
                        path=path,
                        from_id=frontmatter.id,
                        to_id=target,
                        relation=relation,
                        message=f"relation target not found: {target}",
                    )
                )

    return GraphValidationReport(documents=len(documents), links=link_count, issues=tuple(issues))


def split_document_chunks(document: MemoryDocument) -> tuple[IndexedChunk, ...]:
    """Split a parsed Markdown memory into deterministic index chunks."""

    document_id = document.frontmatter.id
    chunks: list[tuple[str, str]] = []
    body = document.body.strip()
    if body:
        chunks.append(("body", body))
        chunks.extend(_section_chunks(body))

    for index, observation in enumerate(document.frontmatter.observations, start=1):
        chunks.append((f"observation:{observation.category}:{index}", observation.text.strip()))

    indexed_chunks: list[IndexedChunk] = []
    for index, (chunk_type, text) in enumerate(chunks, start=1):
        cleaned = _normalize_chunk_text(text)
        if not cleaned:
            continue
        chunk_hash = content_hash(cleaned)
        indexed_chunks.append(
            IndexedChunk(
                id=f"{document_id}:chunk:{index}:{chunk_hash[:12]}",
                document_id=document_id,
                chunk_type=chunk_type,
                text=cleaned,
                token_estimate=estimate_tokens(cleaned),
                content_hash=chunk_hash,
            )
        )
    return tuple(indexed_chunks)


def keyword_search(
    index_path: PathLike,
    query: str,
    *,
    limit: int = 10,
) -> tuple[KeywordSearchResult, ...]:
    """Low-level FTS5 helper for tests and future retrieval services."""

    cleaned_query = query.strip()
    if not cleaned_query:
        return ()
    with sqlite3.connect(Path(index_path)) as connection:
        rows = connection.execute(
            """
            SELECT id, document_id, chunk_type, text
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            LIMIT ?
            """,
            (cleaned_query, limit),
        ).fetchall()
    return tuple(
        KeywordSearchResult(
            chunk_id=row[0],
            document_id=row[1],
            chunk_type=row[2],
            text=row[3],
        )
        for row in rows
    )


def content_hash(value: str) -> str:
    """Return a stable SHA-256 hash for Markdown or chunk content."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Return a small deterministic token estimate without model dependencies."""

    word_count = len(re.findall(r"\S+", text))
    return max(1, int(word_count / 0.75))


def _read_valid_memory_documents(
    config: MemoryConfig,
) -> tuple[tuple[MemoryDocument, str, str], ...]:
    parsed: list[tuple[MemoryDocument, str, str]] = []
    issues: list[ValidationIssue] = []
    for path in iter_memory_markdown_files(config.vault_path):
        try:
            raw_markdown = path.read_text(encoding="utf-8")
            document = parse_markdown_document(raw_markdown, path=path)
            parsed.append((document, raw_markdown, path.relative_to(config.vault_path).as_posix()))
        except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
            issues.append(ValidationIssue(path=path, message=str(exc)))

    if issues:
        joined = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"cannot reindex invalid memory Markdown: {joined}")
    return tuple(parsed)


def _replace_document_index(
    connection: sqlite3.Connection,
    document: MemoryDocument,
    relative_path: str,
    document_hash: str,
) -> None:
    _upsert_document(connection, document, relative_path, document_hash)
    frontmatter = document.frontmatter
    connection.execute(
        """
        INSERT INTO memories (
            id, document_id, type, scope, project, status, confidence, valid_from, valid_to
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            document_id = excluded.document_id,
            type = excluded.type,
            scope = excluded.scope,
            project = excluded.project,
            status = excluded.status,
            confidence = excluded.confidence,
            valid_from = excluded.valid_from,
            valid_to = excluded.valid_to
        """,
        (
            frontmatter.id,
            frontmatter.id,
            _value(frontmatter.type),
            _value(frontmatter.scope),
            frontmatter.project,
            _value(frontmatter.status),
            frontmatter.confidence,
            _optional_isoformat(frontmatter.valid_from),
            _optional_isoformat(frontmatter.valid_to),
        ),
    )


def _upsert_document(
    connection: sqlite3.Connection,
    document: MemoryDocument,
    relative_path: str,
    document_hash: str,
) -> None:
    frontmatter = document.frontmatter
    connection.execute(
        """
        INSERT INTO documents (id, path, type, status, created_at, updated_at, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            path = excluded.path,
            type = excluded.type,
            status = excluded.status,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            content_hash = excluded.content_hash
        """,
        (
            frontmatter.id,
            relative_path,
            _value(frontmatter.type),
            _value(frontmatter.status),
            frontmatter.created_at.isoformat(),
            frontmatter.updated_at.isoformat(),
            document_hash,
        ),
    )


def _replace_chunks(
    connection: sqlite3.Connection,
    document_id: str,
    chunks: Iterable[IndexedChunk],
) -> None:
    connection.execute(
        """
        DELETE FROM embeddings
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)
        """,
        (document_id,),
    )
    connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
    connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    for chunk in chunks:
        connection.execute(
            """
            INSERT INTO chunks (id, document_id, chunk_type, text, token_estimate, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.id,
                chunk.document_id,
                chunk.chunk_type,
                chunk.text,
                chunk.token_estimate,
                chunk.content_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO chunk_fts (id, document_id, chunk_type, text, content_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chunk.id, chunk.document_id, chunk.chunk_type, chunk.text, chunk.content_hash),
        )


def _replace_observations(
    connection: sqlite3.Connection,
    document: MemoryDocument,
    document_hash: str,
) -> int:
    frontmatter = document.frontmatter
    connection.execute("DELETE FROM observations WHERE document_id = ?", (frontmatter.id,))
    for index, observation in enumerate(frontmatter.observations, start=1):
        observation_text = observation.text.strip()
        observation_hash = content_hash(
            f"{document_hash}:{observation.category}:{observation_text}"
        )
        connection.execute(
            """
            INSERT INTO observations (id, document_id, category, text, confidence, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{frontmatter.id}:obs:{index}:{observation_hash[:12]}",
                frontmatter.id,
                observation.category,
                observation_text,
                observation.confidence,
                observation_hash,
            ),
        )
    return len(frontmatter.observations)


def _replace_links(connection: sqlite3.Connection, document: MemoryDocument) -> int:
    frontmatter = document.frontmatter
    connection.execute("DELETE FROM links WHERE from_id = ?", (frontmatter.id,))
    count = 0
    for relation, target, confidence in _iter_relation_edges(frontmatter):
        connection.execute(
            """
            INSERT OR REPLACE INTO links (from_id, to_id, relation, confidence)
            VALUES (?, ?, ?, ?)
            """,
            (frontmatter.id, target, relation, confidence),
        )
        count += 1
    return count


def _iter_relation_edges(frontmatter: Any) -> Iterable[tuple[str, str, Optional[float]]]:
    for relation in frontmatter.relations:
        yield (_value(relation.type), relation.target, relation.confidence)
    for target in frontmatter.supersedes:
        yield (RelationType.SUPERSEDES.value, target, frontmatter.confidence)
    for target in frontmatter.contradicts:
        yield (RelationType.CONTRADICTS.value, target, frontmatter.confidence)


def _delete_document_index(connection: sqlite3.Connection, document_id: str) -> None:
    connection.execute(
        """
        DELETE FROM embeddings
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)
        """,
        (document_id,),
    )
    connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
    connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def _delete_conflicting_path(
    connection: sqlite3.Connection,
    document_id: str,
    relative_path: str,
) -> None:
    row = connection.execute("SELECT id FROM documents WHERE path = ?", (relative_path,)).fetchone()
    if row and row[0] != document_id:
        _delete_document_index(connection, row[0])


def _document_hash(connection: sqlite3.Connection, document_id: str) -> Optional[str]:
    row = connection.execute(
        "SELECT content_hash FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    return str(row[0]) if row else None


def _chunk_count(connection: sqlite3.Connection, document_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def _stale_document_ids(connection: sqlite3.Connection, seen_ids: set[str]) -> tuple[str, ...]:
    rows = connection.execute("SELECT id FROM documents").fetchall()
    return tuple(row[0] for row in rows if row[0] not in seen_ids)


def _section_chunks(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current_title: Optional[str] = None
    current_lines: list[str] = []

    for line in body.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            if current_title and current_lines:
                sections.append((current_title, current_lines))
            current_title = heading.group(2).strip()
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)

    if current_title and current_lines:
        sections.append((current_title, current_lines))

    return [
        (f"section:{_slugify(title)}", "\n".join(lines).strip())
        for title, lines in sections
        if "\n".join(lines).strip()
    ]


def _normalize_chunk_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def _optional_isoformat(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


__all__ = [
    "GraphIssue",
    "GraphValidationReport",
    "IndexedChunk",
    "KeywordSearchResult",
    "ReindexResult",
    "content_hash",
    "ensure_schema",
    "estimate_tokens",
    "keyword_search",
    "reindex_vault",
    "split_document_chunks",
    "validate_graph",
    "validate_graph_documents",
]
