"""Budgeted recall packing for indexed memory chunks."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from agent_memory.config import MemoryConfig, RecallConfig
from agent_memory.indexer import estimate_tokens
from agent_memory.lifecycle import touch_last_used
from agent_memory.retrieval import SearchFilters, search_memory
from agent_memory.schema import RelationType


@dataclass(frozen=True)
class RecallCandidate:
    """A ranked indexed chunk available for budgeted packing."""

    chunk_id: str
    document_id: str
    chunk_type: str
    text: str
    path: str
    memory_type: str
    status: str
    scope: str
    project: Optional[str]
    score: float
    token_estimate: int
    content_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    score_breakdown: Mapping[str, float] = field(default_factory=dict)

    @property
    def citation(self) -> dict[str, Any]:
        return {
            "id": self.document_id,
            "path": self.path,
            "kind": "memory",
            "chunk_id": self.chunk_id,
            "chunk_type": self.chunk_type,
        }


@dataclass(frozen=True)
class PackedChunk:
    """A chunk selected for recall output."""

    chunk_id: str
    document_id: str
    chunk_type: str
    text: str
    path: str
    token_estimate: int
    score: float
    metadata: Mapping[str, Any]
    score_breakdown: Mapping[str, float]
    truncated: bool = False

    @property
    def citation(self) -> dict[str, Any]:
        return {
            "id": self.document_id,
            "path": self.path,
            "kind": "memory",
            "chunk_id": self.chunk_id,
            "chunk_type": self.chunk_type,
            "truncated": self.truncated,
            "token_estimate": self.token_estimate,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.document_id,
            "chunk_id": self.chunk_id,
            "chunk_type": self.chunk_type,
            "text": self.text,
            "path": self.path,
            "token_estimate": self.token_estimate,
            "score": round(self.score, 6),
            "metadata": dict(self.metadata),
            "score_breakdown": {key: round(value, 6) for key, value in self.score_breakdown.items()},
            "truncated": self.truncated,
            "citation": self.citation,
        }


@dataclass(frozen=True)
class RecallResponse:
    """Structured response returned by CLI and MCP recall surfaces."""

    config: MemoryConfig
    query: str
    filters: SearchFilters
    budget: int
    chunks: tuple[PackedChunk, ...]
    candidate_count: int

    @property
    def used_tokens_estimate(self) -> int:
        return sum(chunk.token_estimate for chunk in self.chunks)

    @property
    def citations(self) -> list[dict[str, Any]]:
        return [chunk.citation for chunk in self.chunks]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "query": self.query,
            "filters": self.filters.to_dict(),
            "budget": self.budget,
            "used_tokens_estimate": self.used_tokens_estimate,
            "candidate_count": self.candidate_count,
            "chunk_count": len(self.chunks),
            "vault_path": str(self.config.vault_path),
            "index_path": str(self.config.index_file),
            "packing": {
                "max_tokens_per_chunk": self.config.recall.max_tokens_per_chunk,
                "max_chunks_per_document": self.config.recall.max_chunks_per_document,
                "max_chunks_per_project": self.config.recall.max_chunks_per_project,
                "max_chunks_per_memory_type": dict(self.config.recall.max_chunks_per_memory_type),
                "oversized_chunk_behavior": "truncate_prefix",
            },
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "citations": self.citations,
        }


def recall_memory(
    config: MemoryConfig,
    query: str,
    *,
    filters: Optional[SearchFilters] = None,
    budget: int = 1200,
    include_related: bool = False,
    semantic: Optional[bool] = None,
) -> RecallResponse:
    """Search indexed memory and pack the best chunks under a strict budget."""

    selected_budget = _validate_budget(budget)
    selected_filters = SearchFilters.from_mapping(filters.to_dict()) if filters else SearchFilters()
    search_response = search_memory(
        config,
        query,
        filters=selected_filters,
        include_related=include_related,
        limit=config.recall.candidate_limit,
        semantic=semantic,
    )
    candidates = _candidates_from_search(config, search_response.results)
    if selected_filters.status is None:
        candidates = _remove_superseded_targets(config, candidates)
    chunks = pack_candidates(candidates, budget=selected_budget, recall_config=config.recall)
    try:
        touch_last_used(config, (chunk.document_id for chunk in chunks))
    except Exception:
        pass
    return RecallResponse(
        config=config,
        query=search_response.query,
        filters=selected_filters,
        budget=selected_budget,
        chunks=chunks,
        candidate_count=len(candidates),
    )


def pack_candidates(
    candidates: Sequence[RecallCandidate],
    *,
    budget: int,
    recall_config: Optional[RecallConfig] = None,
) -> tuple[PackedChunk, ...]:
    """Deterministically dedupe, diversify, cap, and pack candidate chunks."""

    selected_budget = _validate_budget(budget)
    config = recall_config or RecallConfig()
    remaining = selected_budget
    packed: list[PackedChunk] = []
    document_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}

    for candidate in _dedupe_near_identical(_rerank_candidates(candidates)):
        if remaining <= 0:
            break
        if document_counts.get(candidate.document_id, 0) >= config.max_chunks_per_document:
            continue
        type_cap = config.max_chunks_per_memory_type.get(candidate.memory_type)
        if type_cap is not None and type_counts.get(candidate.memory_type, 0) >= type_cap:
            continue
        if candidate.project and project_counts.get(candidate.project, 0) >= config.max_chunks_per_project:
            continue

        chunk_budget = min(remaining, config.max_tokens_per_chunk)
        text, token_estimate, truncated = _fit_text(candidate.text, chunk_budget)
        if not text:
            continue

        packed.append(
            PackedChunk(
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                chunk_type=candidate.chunk_type,
                text=text,
                path=candidate.path,
                token_estimate=token_estimate,
                score=_packing_score(candidate),
                metadata=candidate.metadata,
                score_breakdown=candidate.score_breakdown,
                truncated=truncated or token_estimate < candidate.token_estimate,
            )
        )
        remaining -= token_estimate
        document_counts[candidate.document_id] = document_counts.get(candidate.document_id, 0) + 1
        type_counts[candidate.memory_type] = type_counts.get(candidate.memory_type, 0) + 1
        if candidate.project:
            project_counts[candidate.project] = project_counts.get(candidate.project, 0) + 1

    return tuple(packed)


def _candidates_from_search(config: MemoryConfig, results: Sequence[Any]) -> tuple[RecallCandidate, ...]:
    chunk_ids = [str(result.metadata["chunk_id"]) for result in results if result.metadata.get("chunk_id")]
    if not chunk_ids:
        return ()

    placeholders = ", ".join("?" for _ in chunk_ids)
    with sqlite3.connect(config.index_file) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT id, text, token_estimate, content_hash
            FROM chunks
            WHERE id IN ({placeholders})
            """,
            tuple(chunk_ids),
        ).fetchall()

    chunks_by_id = {str(row["id"]): row for row in rows}
    candidates: list[RecallCandidate] = []
    for result in results:
        chunk_id = str(result.metadata.get("chunk_id", ""))
        row = chunks_by_id.get(chunk_id)
        if row is None:
            continue
        metadata = dict(result.metadata)
        candidates.append(
            RecallCandidate(
                chunk_id=chunk_id,
                document_id=result.id,
                chunk_type=str(metadata["chunk_type"]),
                text=str(row["text"]),
                path=result.path,
                memory_type=str(metadata["type"]),
                status=str(metadata["status"]),
                scope=str(metadata["scope"]),
                project=metadata.get("project"),
                score=float(result.score),
                token_estimate=int(row["token_estimate"]),
                content_hash=str(row["content_hash"]),
                metadata=metadata,
                score_breakdown=result.score_breakdown,
            )
        )
    return tuple(candidates)


def _remove_superseded_targets(
    config: MemoryConfig,
    candidates: Sequence[RecallCandidate],
) -> tuple[RecallCandidate, ...]:
    document_ids = {candidate.document_id for candidate in candidates}
    if not document_ids:
        return tuple(candidates)
    placeholders = ", ".join("?" for _ in document_ids)
    with sqlite3.connect(config.index_file) as connection:
        rows = connection.execute(
            f"""
            SELECT to_id
            FROM links
            WHERE relation = ? AND to_id IN ({placeholders})
            """,
            (RelationType.SUPERSEDES.value, *document_ids),
        ).fetchall()
    superseded_ids = {str(row[0]) for row in rows}
    if not superseded_ids:
        return tuple(candidates)
    return tuple(candidate for candidate in candidates if candidate.document_id not in superseded_ids)


def _rerank_candidates(candidates: Sequence[RecallCandidate]) -> list[RecallCandidate]:
    return sorted(candidates, key=lambda candidate: (-_packing_score(candidate), candidate.path, candidate.chunk_id))


def _packing_score(candidate: RecallCandidate) -> float:
    return candidate.score + _chunk_type_boost(candidate.chunk_type)


def _chunk_type_boost(chunk_type: str) -> float:
    if chunk_type == "body":
        return 0.05
    if chunk_type.startswith("observation:"):
        return 0.03
    if chunk_type.startswith("section:"):
        return 0.02
    return 0.0


def _dedupe_near_identical(candidates: Sequence[RecallCandidate]) -> tuple[RecallCandidate, ...]:
    selected: list[RecallCandidate] = []
    signatures: list[tuple[str, ...]] = []
    for candidate in candidates:
        signature = _text_signature(candidate.text)
        if not signature:
            continue
        if any(_signature_similarity(signature, existing) >= 0.92 for existing in signatures):
            continue
        selected.append(candidate)
        signatures.append(signature)
    return tuple(selected)


def _text_signature(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def _signature_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if left == right:
        return 1.0
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    overlap = len(left_set & right_set)
    union = len(left_set | right_set)
    return overlap / union


def _fit_text(text: str, budget: int) -> tuple[str, int, bool]:
    if budget < 1:
        return "", 0, True
    cleaned = text.strip()
    if not cleaned:
        return "", 0, False
    full_estimate = estimate_tokens(cleaned)
    if full_estimate <= budget:
        return cleaned, full_estimate, False

    words = re.findall(r"\S+", cleaned)
    if not words:
        return "", 0, True

    low = 0
    high = len(words)
    best = ""
    best_estimate = 0
    while low <= high:
        midpoint = (low + high) // 2
        candidate = " ".join(words[:midpoint]).strip()
        token_estimate = estimate_tokens(candidate) if candidate else 0
        if candidate and token_estimate <= budget:
            best = candidate
            best_estimate = token_estimate
            low = midpoint + 1
        else:
            high = midpoint - 1

    return best, best_estimate, True


def _validate_budget(value: int) -> int:
    budget = int(value)
    if budget < 1:
        raise ValueError("budget must be at least 1")
    return budget


__all__ = [
    "PackedChunk",
    "RecallCandidate",
    "RecallResponse",
    "pack_candidates",
    "recall_memory",
]
