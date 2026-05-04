"""Shared UX helpers for inspect/open/graph command surfaces."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from config import MemoryConfig
from schema import MemoryDocument, iter_memory_markdown_files, validate_markdown_file, validate_vault


class MemoryNotFoundError(ValueError):
    """Raised when a memory id is not present in the vault."""


def inspect_memory(config: MemoryConfig, memory_id: str) -> dict[str, Any]:
    """Return one canonical memory and stable file links by id."""

    path, document = _find_document(config, memory_id)
    relative_path = path.relative_to(config.vault_path).as_posix()
    return {
        "ok": True,
        "implemented": True,
        "command": "inspect",
        "id": memory_id,
        "found": True,
        "vault_path": str(config.vault_path),
        "path": str(path),
        "relative_path": relative_path,
        "obsidian_uri": _obsidian_uri(path),
        "memory": document.frontmatter.model_dump(mode="json"),
        "body": document.body.strip(),
        "citations": [_citation(memory_id, relative_path)],
    }


def open_memory(config: MemoryConfig, memory_id: str, *, launch: bool = False) -> dict[str, Any]:
    """Resolve a memory to a local Markdown path and optionally launch Obsidian."""

    payload = inspect_memory(config, memory_id)
    target = str(payload["obsidian_uri"] or payload["path"])
    opened = False
    launch_error: Optional[str] = None
    if launch:
        opener = shutil.which("open")
        if opener is None:
            launch_error = "`open` command is not available on this system"
        else:
            completed = subprocess.run(
                [opener, target],
                check=False,
                capture_output=True,
                text=True,
            )
            opened = completed.returncode == 0
            if not opened:
                launch_error = (completed.stderr or completed.stdout or "open command failed").strip()

    return {
        "ok": True,
        "implemented": True,
        "command": "open",
        "id": memory_id,
        "path": payload["path"],
        "relative_path": payload["relative_path"],
        "obsidian_uri": payload["obsidian_uri"],
        "open_target": target,
        "opened": opened,
        "launch_requested": launch,
        "launch_error": launch_error,
        "citations": payload["citations"],
    }


def graph_memory(config: MemoryConfig, memory_id: str) -> dict[str, Any]:
    """Return incoming and outgoing relation links for one memory."""

    indexed = _graph_from_index(config, memory_id)
    if indexed is not None:
        return indexed
    return _graph_from_markdown(config, memory_id)


def _find_document(config: MemoryConfig, memory_id: str) -> tuple[Path, MemoryDocument]:
    for path in iter_memory_markdown_files(config.vault_path):
        document = validate_markdown_file(path)
        if document.frontmatter.id == memory_id:
            return path, document
    raise MemoryNotFoundError(f"memory not found: {memory_id}")


def _graph_from_index(config: MemoryConfig, memory_id: str) -> Optional[dict[str, Any]]:
    if not config.index_file.exists():
        return None

    try:
        connection = sqlite3.connect(config.index_file)
        connection.row_factory = sqlite3.Row
        existing = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        if not {"documents", "links"}.issubset(existing):
            return None
        memory = _document_row(connection, memory_id)
        if memory is None:
            return None
        incoming = _index_links(connection, memory_id, direction="incoming")
        outgoing = _index_links(connection, memory_id, direction="outgoing")
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return _graph_payload(
        config,
        memory_id,
        source="index",
        memory=memory,
        incoming=incoming,
        outgoing=outgoing,
    )


def _graph_from_markdown(config: MemoryConfig, memory_id: str) -> dict[str, Any]:
    report = validate_vault(config.vault_path)
    documents_by_id: dict[str, MemoryDocument] = {}
    paths_by_id: dict[str, str] = {}
    for document in report.documents:
        document_id = document.frontmatter.id
        documents_by_id[document_id] = document
        if document.path is not None:
            paths_by_id[document_id] = document.path.relative_to(config.vault_path).as_posix()

    document = documents_by_id.get(memory_id)
    if document is None:
        raise MemoryNotFoundError(f"memory not found: {memory_id}")

    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    for source in report.documents:
        for edge in _markdown_edges(source):
            if edge["from_id"] != memory_id and edge["to_id"] != memory_id:
                continue
            other_id = edge["to_id"] if edge["from_id"] == memory_id else edge["from_id"]
            link = _link_payload(
                from_id=edge["from_id"],
                to_id=edge["to_id"],
                relation=edge["relation"],
                confidence=edge.get("confidence"),
                direction="outgoing" if edge["from_id"] == memory_id else "incoming",
                other=_document_summary(documents_by_id.get(other_id), paths_by_id),
            )
            if edge["from_id"] == memory_id:
                outgoing.append(link)
            else:
                incoming.append(link)

    memory = _document_summary(document, paths_by_id)
    return _graph_payload(
        config,
        memory_id,
        source="markdown",
        memory=memory,
        incoming=incoming,
        outgoing=outgoing,
    )


def _document_row(connection: sqlite3.Connection, memory_id: str) -> Optional[dict[str, Any]]:
    row = connection.execute(
        """
        SELECT id, path, type, status
        FROM documents
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "path": str(row["path"]),
        "type": str(row["type"]),
        "status": str(row["status"]),
        "citation": _citation(str(row["id"]), str(row["path"])),
    }


def _index_links(connection: sqlite3.Connection, memory_id: str, *, direction: str) -> list[dict[str, Any]]:
    if direction == "outgoing":
        where_sql = "l.from_id = ?"
        other_column = "l.to_id"
    else:
        where_sql = "l.to_id = ?"
        other_column = "l.from_id"

    rows = connection.execute(
        f"""
        SELECT
            l.from_id,
            l.to_id,
            l.relation,
            l.confidence,
            d.id AS other_id,
            d.path AS other_path,
            d.type AS other_type,
            d.status AS other_status
        FROM links l
        LEFT JOIN documents d ON d.id = {other_column}
        WHERE {where_sql}
        ORDER BY l.relation ASC, l.from_id ASC, l.to_id ASC
        """,
        (memory_id,),
    ).fetchall()
    links: list[dict[str, Any]] = []
    for row in rows:
        other = None
        if row["other_id"] is not None:
            other = {
                "id": str(row["other_id"]),
                "path": str(row["other_path"]),
                "type": str(row["other_type"]),
                "status": str(row["other_status"]),
                "citation": _citation(str(row["other_id"]), str(row["other_path"])),
            }
        links.append(
            _link_payload(
                from_id=str(row["from_id"]),
                to_id=str(row["to_id"]),
                relation=str(row["relation"]),
                confidence=_optional_float(row["confidence"]),
                direction=direction,
                other=other,
            )
        )
    return links


def _markdown_edges(document: MemoryDocument) -> list[dict[str, Any]]:
    frontmatter = document.frontmatter
    edges: list[dict[str, Any]] = []
    for relation in frontmatter.relations:
        edges.append(
            {
                "from_id": frontmatter.id,
                "to_id": relation.target,
                "relation": relation.type.value,
                "confidence": relation.confidence,
            }
        )
    for target in frontmatter.supersedes:
        edges.append(
            {
                "from_id": frontmatter.id,
                "to_id": target,
                "relation": "supersedes",
                "confidence": frontmatter.confidence,
            }
        )
    for target in frontmatter.contradicts:
        edges.append(
            {
                "from_id": frontmatter.id,
                "to_id": target,
                "relation": "contradicts",
                "confidence": frontmatter.confidence,
            }
        )
    return edges


def _document_summary(document: Optional[MemoryDocument], paths_by_id: dict[str, str]) -> Optional[dict[str, Any]]:
    if document is None:
        return None
    frontmatter = document.frontmatter
    path = paths_by_id.get(frontmatter.id, "")
    return {
        "id": frontmatter.id,
        "path": path,
        "type": frontmatter.type.value,
        "status": frontmatter.status.value,
        "citation": _citation(frontmatter.id, path) if path else None,
    }


def _link_payload(
    *,
    from_id: str,
    to_id: str,
    relation: str,
    confidence: Optional[float],
    direction: str,
    other: Optional[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "from_id": from_id,
        "to_id": to_id,
        "relation": relation,
        "confidence": confidence,
        "direction": direction,
        "other": other,
    }


def _graph_payload(
    config: MemoryConfig,
    memory_id: str,
    *,
    source: str,
    memory: dict[str, Any],
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
) -> dict[str, Any]:
    citations = [memory["citation"]]
    for link in (*outgoing, *incoming):
        other = link.get("other")
        if other and other.get("citation"):
            citations.append(other["citation"])
    return {
        "ok": True,
        "implemented": True,
        "command": "graph",
        "id": memory_id,
        "found": True,
        "source": source,
        "vault_path": str(config.vault_path),
        "index_path": str(config.index_file),
        "memory": memory,
        "outgoing": outgoing,
        "incoming": incoming,
        "link_count": len(incoming) + len(outgoing),
        "citations": _dedupe_citations(citations),
    }


def _obsidian_uri(path: Path) -> str:
    return "obsidian://open?path=" + quote(str(path), safe="")


def _citation(memory_id: str, path: str) -> dict[str, str]:
    return {"id": memory_id, "path": path, "kind": "memory"}


def _dedupe_citations(citations: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for citation in citations:
        key = (citation["id"], citation["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


__all__ = [
    "MemoryNotFoundError",
    "graph_memory",
    "inspect_memory",
    "open_memory",
]
