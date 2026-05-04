"""Budgeted recall packing for indexed memory chunks."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import yaml

from config import MemoryConfig, RecallConfig
from indexer import estimate_tokens
from lifecycle import touch_last_used
from retrieval import SearchFilters, search_memory
from schema import LifecycleStatus, RelationType
from session import SessionRecallState, normalize_session_recall_state, session_trace


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
class PackingTrace:
    """A deterministic packing decision for explainability surfaces."""

    action: str
    reason: str
    candidate: RecallCandidate
    details: Mapping[str, Any] = field(default_factory=dict)
    packed_chunk: Optional[PackedChunk] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action": self.action,
            "reason": self.reason,
            "id": self.candidate.document_id,
            "chunk_id": self.candidate.chunk_id,
            "chunk_type": self.candidate.chunk_type,
            "path": self.candidate.path,
            "score": round(_packing_score(self.candidate), 6),
            "token_estimate": self.candidate.token_estimate,
            "metadata": dict(self.candidate.metadata),
            "score_breakdown": {
                key: round(value, 6) for key, value in self.candidate.score_breakdown.items()
            },
            "details": dict(self.details),
            "citation": self.candidate.citation,
        }
        if self.packed_chunk is not None:
            payload["packed_chunk"] = self.packed_chunk.to_dict()
        return payload


@dataclass(frozen=True)
class PackingResult:
    """Packed chunks plus a trace of selected and skipped candidates."""

    chunks: tuple[PackedChunk, ...]
    trace: tuple[PackingTrace, ...]


@dataclass(frozen=True)
class RecallResponse:
    """Structured response returned by CLI recall surfaces."""

    config: MemoryConfig
    query: str
    filters: SearchFilters
    budget: int
    chunks: tuple[PackedChunk, ...]
    candidate_count: int
    retrieval_trace: Mapping[str, Any] = field(default_factory=dict)
    session: Mapping[str, Any] = field(default_factory=dict)

    @property
    def used_tokens_estimate(self) -> int:
        return sum(chunk.token_estimate for chunk in self.chunks)

    @property
    def citations(self) -> list[dict[str, Any]]:
        return [chunk.citation for chunk in self.chunks]

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
            "retrieval": dict(self.retrieval_trace),
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "citations": self.citations,
        }
        if self.session:
            payload["session"] = dict(self.session)
        return payload


@dataclass(frozen=True)
class ExplainRecallResponse:
    """Structured explanation for why recall packed or skipped candidates."""

    config: MemoryConfig
    query: str
    filters: SearchFilters
    budget: int
    chunks: tuple[PackedChunk, ...]
    trace: tuple[PackingTrace, ...]
    candidate_count: int
    semantic_enabled: bool
    semantic_provider: Optional[str]
    retrieval_trace: Mapping[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> tuple[PackingTrace, ...]:
        return tuple(item for item in self.trace if item.action == "selected")

    @property
    def skipped(self) -> tuple[PackingTrace, ...]:
        return tuple(item for item in self.trace if item.action == "skipped")

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
            "command": "explain_recall",
            "query": self.query,
            "filters": self.filters.to_dict(),
            "budget": self.budget,
            "used_tokens_estimate": self.used_tokens_estimate,
            "candidate_count": self.candidate_count,
            "selected_count": len(self.selected),
            "skipped_count": len(self.skipped),
            "vault_path": str(self.config.vault_path),
            "index_path": str(self.config.index_file),
            "semantic": {
                "enabled": self.semantic_enabled,
                "provider": self.semantic_provider,
            },
            "retrieval": dict(self.retrieval_trace),
            "selected": [
                {
                    **item.to_dict(),
                    "explanation": _selected_explanation(
                        item.candidate,
                        item.packed_chunk,
                        filters=self.filters,
                    ),
                }
                for item in self.selected
            ],
            "skipped": [
                {
                    **item.to_dict(),
                    "explanation": _skipped_explanation(item),
                }
                for item in self.skipped
            ],
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
    mode: str = "auto",
    session_id: Any = None,
    loaded_memory_ids: Any = None,
    loaded_source_ids: Any = None,
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
        mode=mode,
    )
    candidates = _candidates_from_search(config, search_response.results)
    if selected_filters.status is None:
        candidates = _remove_superseded_targets(config, candidates)
    candidate_count_before_session_filter = len(candidates)
    session_state = normalize_session_recall_state(
        session_id=session_id,
        loaded_memory_ids=loaded_memory_ids,
        loaded_source_ids=loaded_source_ids,
    )
    candidates, selected_session_trace = _apply_session_dedupe(candidates, session_state)
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
        retrieval_trace=search_response.trace_to_dict(),
        session=session_trace(
            session_state,
            filtered_memory_ids=selected_session_trace["filtered_memory_ids"],
            filtered_source_ids=selected_session_trace["filtered_source_ids"],
            candidate_count_before=candidate_count_before_session_filter,
            candidate_count_after=len(candidates),
        ),
    )


def explain_recall(
    config: MemoryConfig,
    query: str,
    *,
    filters: Optional[SearchFilters] = None,
    budget: int = 1200,
    include_related: bool = False,
    semantic: Optional[bool] = None,
    mode: str = "auto",
    max_skipped: int = 5,
) -> ExplainRecallResponse:
    """Explain deterministic retrieval and packing decisions without touching memory files."""

    selected_budget = _validate_budget(budget)
    selected_filters = SearchFilters.from_mapping(filters.to_dict()) if filters else SearchFilters()
    search_response = search_memory(
        config,
        query,
        filters=selected_filters,
        include_related=include_related,
        limit=config.recall.candidate_limit,
        semantic=semantic,
        mode=mode,
        include_superseded_targets=True,
    )
    candidates = _candidates_from_search(config, search_response.results)
    trace_prefix: list[PackingTrace] = []
    packable = candidates
    if selected_filters.status is None:
        superseded_by = _superseded_replacements(config, candidates)
        if superseded_by:
            packable_items: list[RecallCandidate] = []
            for candidate in candidates:
                replacement = superseded_by.get(candidate.document_id)
                if replacement is None:
                    packable_items.append(candidate)
                    continue
                trace_prefix.append(
                    PackingTrace(
                        action="skipped",
                        reason="superseded",
                        candidate=candidate,
                        details={"superseded_by": replacement},
                    )
                )
            packable = tuple(packable_items)

    packing = pack_candidates_with_trace(packable, budget=selected_budget, recall_config=config.recall)
    selected_trace = tuple(item for item in packing.trace if item.action == "selected")
    skipped_trace = [
        *trace_prefix,
        *(item for item in packing.trace if item.action == "skipped"),
    ][:max_skipped]
    trace_suffix = _status_filtered_traces(
        config,
        query,
        selected_filters,
        include_related=include_related,
        semantic=semantic,
        mode=mode,
        excluded_chunk_ids={item.candidate.chunk_id for item in (*selected_trace, *skipped_trace)},
        limit=max(0, max_skipped - len(skipped_trace)),
    )
    return ExplainRecallResponse(
        config=config,
        query=search_response.query,
        filters=selected_filters,
        budget=selected_budget,
        chunks=packing.chunks,
        trace=selected_trace + tuple(skipped_trace) + trace_suffix,
        candidate_count=len(candidates),
        semantic_enabled=search_response.semantic_enabled,
        semantic_provider=search_response.semantic_provider,
        retrieval_trace=search_response.trace_to_dict(),
    )


def pack_candidates(
    candidates: Sequence[RecallCandidate],
    *,
    budget: int,
    recall_config: Optional[RecallConfig] = None,
) -> tuple[PackedChunk, ...]:
    """Deterministically dedupe, diversify, cap, and pack candidate chunks."""

    return pack_candidates_with_trace(candidates, budget=budget, recall_config=recall_config).chunks


def pack_candidates_with_trace(
    candidates: Sequence[RecallCandidate],
    *,
    budget: int,
    recall_config: Optional[RecallConfig] = None,
) -> PackingResult:
    """Deterministically pack candidates and keep reasons for skipped chunks."""

    selected_budget = _validate_budget(budget)
    config = recall_config or RecallConfig()
    remaining = selected_budget
    packed: list[PackedChunk] = []
    trace: list[PackingTrace] = []
    document_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    signatures: list[tuple[str, ...]] = []
    signature_sources: list[str] = []

    for candidate in _rerank_candidates(candidates):
        signature = _text_signature(candidate.text)
        if not signature:
            trace.append(PackingTrace(action="skipped", reason="empty", candidate=candidate))
            continue
        duplicate_of = _duplicate_signature_source(signature, signatures, signature_sources)
        if duplicate_of is not None:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="duplicate",
                    candidate=candidate,
                    details={"duplicate_of": duplicate_of},
                )
            )
            continue
        signatures.append(signature)
        signature_sources.append(candidate.document_id)

        if remaining <= 0:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="over_budget",
                    candidate=candidate,
                    details={"remaining_tokens": 0},
                )
            )
            continue
        if document_counts.get(candidate.document_id, 0) >= config.max_chunks_per_document:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="cap_filtered",
                    candidate=candidate,
                    details={
                        "cap": "document",
                        "limit": config.max_chunks_per_document,
                        "value": candidate.document_id,
                    },
                )
            )
            continue
        type_cap = config.max_chunks_per_memory_type.get(candidate.memory_type)
        if type_cap is not None and type_counts.get(candidate.memory_type, 0) >= type_cap:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="cap_filtered",
                    candidate=candidate,
                    details={"cap": "memory_type", "limit": type_cap, "value": candidate.memory_type},
                )
            )
            continue
        if candidate.project and project_counts.get(candidate.project, 0) >= config.max_chunks_per_project:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="cap_filtered",
                    candidate=candidate,
                    details={
                        "cap": "project",
                        "limit": config.max_chunks_per_project,
                        "value": candidate.project,
                    },
                )
            )
            continue

        chunk_budget = min(remaining, config.max_tokens_per_chunk)
        text, token_estimate, truncated = _fit_text(candidate.text, chunk_budget)
        if not text:
            trace.append(
                PackingTrace(
                    action="skipped",
                    reason="over_budget",
                    candidate=candidate,
                    details={"remaining_tokens": remaining},
                )
            )
            continue

        packed_chunk = PackedChunk(
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
        packed.append(packed_chunk)
        trace.append(
            PackingTrace(
                action="selected",
                reason="selected",
                candidate=candidate,
                details={"remaining_tokens_before": remaining},
                packed_chunk=packed_chunk,
            )
        )
        remaining -= token_estimate
        document_counts[candidate.document_id] = document_counts.get(candidate.document_id, 0) + 1
        type_counts[candidate.memory_type] = type_counts.get(candidate.memory_type, 0) + 1
        if candidate.project:
            project_counts[candidate.project] = project_counts.get(candidate.project, 0) + 1

    return PackingResult(chunks=tuple(packed), trace=tuple(trace))


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
        metadata = {**dict(result.metadata), **_source_metadata(config, result.path)}
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


def _apply_session_dedupe(
    candidates: Sequence[RecallCandidate],
    state: SessionRecallState,
) -> tuple[tuple[RecallCandidate, ...], dict[str, tuple[str, ...]]]:
    """Filter candidates the client says are already loaded."""

    if not state.loaded_memory_ids and not state.loaded_source_ids:
        return tuple(candidates), {"filtered_memory_ids": (), "filtered_source_ids": ()}

    loaded_memory_ids = state.loaded_memory_id_set
    loaded_source_ids = state.loaded_source_id_set
    filtered_memory_ids: list[str] = []
    filtered_source_ids: list[str] = []
    selected: list[RecallCandidate] = []
    for candidate in candidates:
        source_id = _candidate_source_id(candidate)
        memory_loaded = candidate.document_id in loaded_memory_ids
        source_loaded = source_id in loaded_source_ids if source_id is not None else False
        if memory_loaded or source_loaded:
            filtered_memory_ids.append(candidate.document_id)
            if source_loaded and source_id is not None:
                filtered_source_ids.append(source_id)
            continue
        selected.append(candidate)
    return (
        tuple(selected),
        {
            "filtered_memory_ids": tuple(dict.fromkeys(filtered_memory_ids)),
            "filtered_source_ids": tuple(dict.fromkeys(filtered_source_ids)),
        },
    )


def _candidate_source_id(candidate: RecallCandidate) -> Optional[str]:
    value = candidate.metadata.get("source_id")
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _source_metadata(config: MemoryConfig, relative_path: str) -> dict[str, str]:
    path = config.vault_path / relative_path
    try:
        text = path.read_text(encoding="utf-8")
        frontmatter = _frontmatter_mapping(text)
    except Exception:
        return {}
    source = frontmatter.get("source")
    if not isinstance(source, Mapping):
        return {}

    source_path = _optional_source_string(source.get("path"))
    source_id = _optional_source_string(source.get("source_id")) or _source_id_from_path(source_path)
    metadata: dict[str, str] = {}
    if source_id is not None:
        metadata["source_id"] = source_id
    if source_path is not None:
        metadata["source_path"] = source_path
    return metadata


def _frontmatter_mapping(text: str) -> Mapping[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    payload = yaml.safe_load(parts[0][4:]) or {}
    return payload if isinstance(payload, Mapping) else {}


def _source_id_from_path(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "Sources" and parts[1] not in {"", ".", ".."}:
        return parts[1]
    return None


def _optional_source_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _remove_superseded_targets(
    config: MemoryConfig,
    candidates: Sequence[RecallCandidate],
) -> tuple[RecallCandidate, ...]:
    superseded_by = _superseded_replacements(config, candidates)
    if not superseded_by:
        return tuple(candidates)
    return tuple(candidate for candidate in candidates if candidate.document_id not in superseded_by)


def _superseded_replacements(
    config: MemoryConfig,
    candidates: Sequence[RecallCandidate],
) -> dict[str, str]:
    document_ids = {candidate.document_id for candidate in candidates}
    if not document_ids:
        return {}
    placeholders = ", ".join("?" for _ in document_ids)
    with sqlite3.connect(config.index_file) as connection:
        rows = connection.execute(
            f"""
            SELECT from_id, to_id
            FROM links
            WHERE relation = ? AND to_id IN ({placeholders})
            """,
            (RelationType.SUPERSEDES.value, *document_ids),
        ).fetchall()
    return {str(row[1]): str(row[0]) for row in rows}


def _status_filtered_traces(
    config: MemoryConfig,
    query: str,
    filters: SearchFilters,
    *,
    include_related: bool,
    semantic: Optional[bool],
    mode: str,
    excluded_chunk_ids: set[str],
    limit: int,
) -> tuple[PackingTrace, ...]:
    if limit <= 0 or filters.status is not None:
        return ()

    traces: list[PackingTrace] = []
    for status in (
        LifecycleStatus.PENDING.value,
        LifecycleStatus.REJECTED.value,
        LifecycleStatus.SUPERSEDED.value,
    ):
        response = search_memory(
            config,
            query,
            filters=_with_status(filters, status),
            include_related=include_related,
            limit=max(3, min(config.recall.candidate_limit, limit)),
            semantic=semantic,
            mode=mode,
            include_superseded_targets=True,
        )
        for candidate in _candidates_from_search(config, response.results):
            if candidate.chunk_id in excluded_chunk_ids:
                continue
            traces.append(
                PackingTrace(
                    action="skipped",
                    reason="status_filtered",
                    candidate=candidate,
                    details={"status": status},
                )
            )
            excluded_chunk_ids.add(candidate.chunk_id)
            if len(traces) >= limit:
                return tuple(traces)
    return tuple(traces)


def _with_status(filters: SearchFilters, status: str) -> SearchFilters:
    return SearchFilters(
        project=filters.project,
        memory_type=filters.memory_type,
        status=status,
        scope=filters.scope,
        created_after=filters.created_after,
        created_before=filters.created_before,
        updated_after=filters.updated_after,
        updated_before=filters.updated_before,
        valid_from=filters.valid_from,
        valid_to=filters.valid_to,
    )


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


def _duplicate_signature_source(
    signature: tuple[str, ...],
    signatures: Sequence[tuple[str, ...]],
    sources: Sequence[str],
) -> Optional[str]:
    for existing, source in zip(signatures, sources):
        if _signature_similarity(signature, existing) >= 0.92:
            return source
    return None


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


def _selected_explanation(
    candidate: RecallCandidate,
    chunk: Optional[PackedChunk],
    *,
    filters: SearchFilters,
) -> str:
    reasons: list[str] = []
    semantic_similarity = candidate.metadata.get("semantic_similarity")
    if semantic_similarity is not None:
        reasons.append(f"semantic score {float(semantic_similarity):.2f}")
    elif candidate.score_breakdown.get("fts_score", 0.0) > 0.0:
        reasons.append(f"keyword score {candidate.score_breakdown['fts_score']:.2f}")
    else:
        reasons.append(f"score {_packing_score(candidate):.2f}")

    reasons.append(f"{candidate.status} {candidate.memory_type.replace('_', ' ')}")
    if filters.project and candidate.project == filters.project:
        reasons.append("project match")
    elif candidate.project:
        reasons.append(f"project {candidate.project}")
    if candidate.metadata.get("related"):
        relation = candidate.metadata.get("relation") or "relation"
        reasons.append(f"graph {relation}")
    if chunk and chunk.truncated:
        reasons.append("truncated to budget")
    return f"Selected chunk {candidate.document_id} because {', '.join(reasons)}."


def _skipped_explanation(trace: PackingTrace) -> str:
    candidate = trace.candidate
    details = dict(trace.details)
    if trace.reason == "superseded":
        return f"Skipped chunk {candidate.document_id} because superseded by {details.get('superseded_by')}."
    if trace.reason == "duplicate":
        return f"Skipped chunk {candidate.document_id} because duplicate of {details.get('duplicate_of')}."
    if trace.reason == "over_budget":
        return f"Skipped chunk {candidate.document_id} because over budget."
    if trace.reason == "cap_filtered":
        cap = details.get("cap", "packing")
        limit = details.get("limit")
        return f"Skipped chunk {candidate.document_id} because {cap} cap reached (max {limit})."
    if trace.reason == "empty":
        return f"Skipped chunk {candidate.document_id} because it had no searchable text."
    if trace.reason == "status_filtered":
        status = details.get("status", candidate.status)
        return f"Skipped chunk {candidate.document_id} because status {status} was filtered."
    return f"Skipped chunk {candidate.document_id} because {trace.reason}."


__all__ = [
    "ExplainRecallResponse",
    "PackedChunk",
    "PackingResult",
    "PackingTrace",
    "RecallCandidate",
    "RecallResponse",
    "explain_recall",
    "pack_candidates",
    "pack_candidates_with_trace",
    "recall_memory",
]
