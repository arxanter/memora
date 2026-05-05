"""Stage 5 retrieval service backed by the disposable SQLite index."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, Mapping, Optional, Sequence

import yaml

from config import (
    DEFAULT_SEMANTIC_KEYWORD_LIMIT,
    DEFAULT_SEMANTIC_MIN_SIMILARITY,
    DEFAULT_SEMANTIC_VECTOR_LIMIT,
    MemoryConfig,
)
from embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    cosine_similarity,
    deserialize_vector,
    provider_from_config,
    serialize_vector,
)
from safety import has_unsafe_recall_risk, normalize_risk_flags, scan_metadata, scan_text
from schema import LifecycleStatus, MemoryScope, MemoryType, RelationType

DEFAULT_STATUSES = (LifecycleStatus.ACTIVE.value, LifecycleStatus.STALE.value)
REQUIRED_INDEX_OBJECTS = ("documents", "chunks", "memories", "links", "chunk_fts")
SearchMode = Literal["auto", "text", "vector", "hybrid"]
SEARCH_MODES = ("auto", "text", "vector", "hybrid")
SEMANTIC_SEARCH_MODES = {"vector", "hybrid"}
STRONG_RESULT_SCORE = 7.0
MAX_QUERY_VARIANTS = 5

QUERY_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "before",
    "by",
    "can",
    "could",
    "decide",
    "decided",
    "did",
    "do",
    "does",
    "find",
    "for",
    "from",
    "give",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "need",
    "needs",
    "of",
    "on",
    "or",
    "our",
    "please",
    "recall",
    "remember",
    "remembered",
    "show",
    "that",
    "the",
    "these",
    "this",
    "those",
    "to",
    "use",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
}

MEMORY_TYPE_BOOSTS = {
    MemoryType.PREFERENCE.value: 0.35,
    MemoryType.DECISION.value: 0.30,
    MemoryType.PROJECT_CONTEXT.value: 0.25,
    MemoryType.FACT.value: 0.15,
    MemoryType.CONVERSATION_SUMMARY.value: 0.10,
    MemoryType.TASK.value: 0.05,
}

STATUS_BOOSTS = {
    LifecycleStatus.ACTIVE.value: 0.40,
    LifecycleStatus.STALE.value: 0.00,
    LifecycleStatus.PENDING.value: -0.20,
    LifecycleStatus.SUPERSEDED.value: -0.50,
    LifecycleStatus.REJECTED.value: -3.00,
}


class RetrievalIndexError(RuntimeError):
    """Raised when the SQLite retrieval index is missing or incomplete."""


@dataclass(frozen=True)
class SearchFilters:
    """Metadata filters accepted by CLI search surfaces."""

    project: Optional[str] = None
    memory_type: Optional[str] = None
    status: Optional[str] = None
    scope: Optional[str] = None
    created_after: Optional[str] = None
    created_before: Optional[str] = None
    updated_after: Optional[str] = None
    updated_before: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None

    @classmethod
    def from_mapping(cls, values: Optional[Mapping[str, Any]]) -> "SearchFilters":
        payload = dict(values or {})
        return cls(
            project=_optional_string(payload.get("project")),
            memory_type=_optional_enum_value(
                MemoryType, payload.get("type") or payload.get("memory_type")
            ),
            status=_optional_enum_value(LifecycleStatus, payload.get("status")),
            scope=_optional_enum_value(MemoryScope, payload.get("scope")),
            created_after=_optional_datetime_filter(payload.get("created_after")),
            created_before=_optional_datetime_filter(
                payload.get("created_before"), end_of_day=True
            ),
            updated_after=_optional_datetime_filter(payload.get("updated_after")),
            updated_before=_optional_datetime_filter(
                payload.get("updated_before"), end_of_day=True
            ),
            valid_from=_optional_date_filter(payload.get("valid_from")),
            valid_to=_optional_date_filter(payload.get("valid_to")),
        )

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for field_name, value in (
            ("project", self.project),
            ("type", self.memory_type),
            ("status", self.status),
            ("scope", self.scope),
            ("created_after", self.created_after),
            ("created_before", self.created_before),
            ("updated_after", self.updated_after),
            ("updated_before", self.updated_before),
            ("valid_from", self.valid_from),
            ("valid_to", self.valid_to),
        ):
            if value is not None:
                payload[field_name] = value
        return payload


@dataclass(frozen=True)
class SearchResult:
    id: str
    score: float
    snippet: str
    path: str
    metadata: dict[str, Any]
    score_breakdown: dict[str, float]
    related: bool = False

    @property
    def citation(self) -> dict[str, str]:
        return {"id": self.id, "path": self.path, "kind": "memory"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": round(self.score, 6),
            "snippet": self.snippet,
            "citation": self.citation,
            "path": self.path,
            "metadata": self.metadata,
            "score_breakdown": {
                key: round(value, 6) for key, value in self.score_breakdown.items()
            },
            "related": self.related,
        }


@dataclass(frozen=True)
class QueryPlan:
    """Deterministic query variants used by fallback retrieval."""

    original: str
    variants: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "variants": list(self.variants),
        }


@dataclass(frozen=True)
class SearchAttempt:
    """One query variant attempted by the retrieval service."""

    query: str
    mode: str
    reason: str
    result_count: int
    strong_result_count: int
    text_searched: bool
    vector_searched: bool
    fallback_trigger: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "query": self.query,
            "mode": self.mode,
            "reason": self.reason,
            "result_count": self.result_count,
            "strong_result_count": self.strong_result_count,
            "text_searched": self.text_searched,
            "vector_searched": self.vector_searched,
        }
        if self.fallback_trigger is not None:
            payload["fallback_trigger"] = self.fallback_trigger
        return payload


@dataclass(frozen=True)
class SearchResponse:
    config: MemoryConfig
    query: str
    filters: SearchFilters
    include_related: bool
    results: tuple[SearchResult, ...]
    mode: str = "text"
    requested_mode: str = "auto"
    semantic_enabled: bool = False
    semantic_provider: Optional[str] = None
    semantic_model: Optional[str] = None
    query_plan: Optional[QueryPlan] = None
    attempted_searches: tuple[SearchAttempt, ...] = ()
    empty_reason: Optional[str] = None

    @property
    def citations(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        citations: list[dict[str, str]] = []
        for result in self.results:
            if result.id in seen:
                continue
            seen.add(result.id)
            citations.append(result.citation)
        return citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "query": self.query,
            "filters": self.filters.to_dict(),
            "include_related": self.include_related,
            "mode": self.mode,
            "requested_mode": self.requested_mode,
            "semantic": {
                "enabled": self.semantic_enabled,
                "provider": self.semantic_provider,
                "model": self.semantic_model,
            },
            "query_plan": (self.query_plan or QueryPlan(self.query, (self.query,))).to_dict(),
            "attempted_searches": [attempt.to_dict() for attempt in self.attempted_searches],
            "trace": self.trace_to_dict(),
            "empty_reason": self.empty_reason,
            "result_count": len(self.results),
            "vault_path": str(self.config.vault_path),
            "index_path": str(self.config.index_file),
            "results": [result.to_dict() for result in self.results],
            "citations": self.citations,
        }

    def trace_to_dict(self) -> dict[str, Any]:
        plan = self.query_plan or QueryPlan(self.query, (self.query,))
        return {
            "query": self.query,
            "planned_query_variants": list(plan.variants),
            "mode": self.mode,
            "requested_mode": self.requested_mode,
            "semantic": {
                "status": _semantic_status(
                    self.mode, self.semantic_enabled, self.semantic_provider
                ),
                "enabled": self.semantic_enabled,
                "provider": self.semantic_provider,
                "model": self.semantic_model,
            },
            "attempted_searches": [attempt.to_dict() for attempt in self.attempted_searches],
            "selected_count": len(self.results),
            "empty_reason": self.empty_reason,
        }


@dataclass(frozen=True)
class _Candidate:
    document_id: str
    chunk_id: str
    chunk_type: str
    text: str
    snippet: str
    path: str
    memory_type: str
    status: str
    created_at: str
    updated_at: str
    scope: str
    project: Optional[str]
    confidence: Optional[float]
    valid_from: Optional[str]
    valid_to: Optional[str]
    fts_score: float
    fts_rank: float
    row_order: int
    semantic_score: float = 0.0
    semantic_similarity: Optional[float] = None
    related: bool = False
    relation: Optional[str] = None
    related_to: Optional[str] = None
    relation_direction: Optional[str] = None
    relation_confidence: Optional[float] = None


def search_memory(
    config: MemoryConfig,
    query: str,
    *,
    filters: Optional[SearchFilters] = None,
    include_related: bool = False,
    limit: int = 10,
    semantic: Optional[bool] = None,
    mode: str = "auto",
    embedding_provider: Optional[EmbeddingProvider] = None,
    include_superseded_targets: bool = False,
) -> SearchResponse:
    """Search indexed memory chunks and return ranked memory-level results."""

    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("search query must not be empty")
    selected_filters = SearchFilters.from_mapping(filters.to_dict()) if filters else SearchFilters()
    selected_limit = int(limit)
    if selected_limit < 1:
        raise ValueError("limit must be at least 1")

    query_plan = plan_query_variants(cleaned_query)
    requested_mode, effective_mode = _resolve_search_mode(config, mode=mode, semantic=semantic)
    semantic_enabled = effective_mode in SEMANTIC_SEARCH_MODES
    provider: Optional[EmbeddingProvider] = None
    if semantic_enabled:
        try:
            provider = embedding_provider or provider_from_config(config.semantic)
        except EmbeddingProviderError:
            if requested_mode != "auto":
                raise
            effective_mode = "text"
            semantic_enabled = False
    attempts: list[SearchAttempt] = []
    results_by_id: dict[str, SearchResult] = {}
    fallback_trigger: Optional[str] = None

    for index, planned_query in enumerate(query_plan.variants):
        current_results = _merged_results(results_by_id, selected_limit)
        if index > 0:
            if not _needs_fallback(current_results, selected_limit):
                break
            fallback_trigger = fallback_trigger or _fallback_reason(current_results, selected_limit)

        try:
            attempt_results = _search_memory_once(
                config,
                planned_query,
                selected_filters,
                include_related=include_related,
                limit=selected_limit,
                mode=effective_mode,
                provider=provider,
                include_superseded_targets=include_superseded_targets,
            )
        except EmbeddingProviderError:
            if requested_mode != "auto":
                raise
            effective_mode = "text"
            semantic_enabled = False
            provider = None
            attempt_results = _search_memory_once(
                config,
                planned_query,
                selected_filters,
                include_related=include_related,
                limit=selected_limit,
                mode=effective_mode,
                provider=provider,
                include_superseded_targets=include_superseded_targets,
            )
        _merge_results_by_id(results_by_id, attempt_results)
        attempts.append(
            SearchAttempt(
                query=planned_query,
                mode=effective_mode,
                reason="original" if index == 0 else "fallback",
                result_count=len(attempt_results),
                strong_result_count=_strong_result_count(attempt_results),
                text_searched=effective_mode in {"text", "hybrid"},
                vector_searched=effective_mode in SEMANTIC_SEARCH_MODES,
                fallback_trigger=None if index == 0 else fallback_trigger,
            )
        )

    results = _safety_annotated_results(
        config,
        _merged_results(results_by_id, selected_limit),
        filter_unsafe=selected_filters.status is None,
    )
    return SearchResponse(
        config=config,
        query=cleaned_query,
        filters=selected_filters,
        include_related=include_related,
        results=tuple(results),
        mode=effective_mode,
        requested_mode=requested_mode,
        semantic_enabled=semantic_enabled,
        semantic_provider=_provider_name(provider, config) if provider is not None else None,
        semantic_model=provider.model if provider is not None else None,
        query_plan=query_plan,
        attempted_searches=tuple(attempts),
        empty_reason="no_results" if not results else None,
    )


def plan_query_variants(query: str, *, max_variants: int = MAX_QUERY_VARIANTS) -> QueryPlan:
    """Build lightweight deterministic variants for natural-language retrieval."""

    original = _collapse_whitespace(query)
    if not original:
        raise ValueError("search query must not be empty")

    variants: list[str] = []
    _add_query_variant(variants, original, max_variants=max_variants)

    normalized_tokens = _query_tokens(original)
    normalized = " ".join(normalized_tokens)
    _add_query_variant(variants, normalized, max_variants=max_variants)

    singular_tokens = [_normalize_query_token(token) for token in normalized_tokens]
    singular = " ".join(singular_tokens)
    _add_query_variant(variants, singular, max_variants=max_variants)

    informative_tokens = [token for token in singular_tokens if token not in QUERY_STOPWORDS]
    if len(informative_tokens) >= 2:
        _add_query_variant(variants, " ".join(informative_tokens), max_variants=max_variants)

    acronym_expanded = _expand_common_acronyms(informative_tokens)
    if len(acronym_expanded) >= 2:
        _add_query_variant(variants, " ".join(acronym_expanded), max_variants=max_variants)

    return QueryPlan(original=original, variants=tuple(variants))


def _search_memory_once(
    config: MemoryConfig,
    query: str,
    filters: SearchFilters,
    *,
    include_related: bool,
    limit: int,
    mode: str,
    provider: Optional[EmbeddingProvider],
    include_superseded_targets: bool,
) -> tuple[SearchResult, ...]:
    fts_query = _to_fts_query(query) if mode in {"text", "hybrid"} else None
    semantic_enabled = mode in SEMANTIC_SEARCH_MODES
    connection = _connect_index(config)
    try:
        text_primary: list[_Candidate] = []
        if fts_query is not None:
            keyword_limit = DEFAULT_SEMANTIC_KEYWORD_LIMIT if semantic_enabled else max(limit * 8, 40)
            text_primary = _primary_candidates(
                connection,
                fts_query,
                filters,
                fetch_limit=max(limit * 8, keyword_limit),
            )

        semantic_primary: list[_Candidate] = []
        if semantic_enabled and provider is not None:
            semantic_primary = _semantic_candidates(
                connection,
                query,
                filters,
                provider=provider,
                fetch_limit=max(limit * 8, DEFAULT_SEMANTIC_VECTOR_LIMIT),
                min_similarity=DEFAULT_SEMANTIC_MIN_SIMILARITY,
            )

        primary = _merge_candidates(text_primary, semantic_primary)
        candidates = list(primary)
        if include_related and primary:
            candidates.extend(
                _related_candidates(
                    connection,
                    primary,
                    filters,
                    max_related=max(limit * 2, 10),
                )
            )

        reference_time = _reference_time(candidates)
        primary_ids = {candidate.document_id for candidate in primary}
        connected_primary_ids = _connected_document_ids(connection, primary_ids)
        superseded_ids = _superseded_document_ids(
            connection, {candidate.document_id for candidate in candidates}
        )
    finally:
        connection.close()

    if filters.status is None and superseded_ids and not include_superseded_targets:
        candidates = [
            candidate for candidate in candidates if candidate.document_id not in superseded_ids
        ]
    results = [
        _rank_candidate(candidate, reference_time, connected_primary_ids, superseded_ids)
        for candidate in candidates
    ]
    results.sort(key=lambda result: (-result.score, result.related, result.id))
    return tuple(results[:limit])


def _resolve_search_mode(
    config: MemoryConfig,
    *,
    mode: str,
    semantic: Optional[bool],
) -> tuple[str, str]:
    if semantic is True:
        return "semantic:true", "hybrid"
    if semantic is False:
        return "semantic:false", "text"

    requested_mode = mode.strip().lower()
    if requested_mode not in SEARCH_MODES:
        raise ValueError("mode must be one of: auto, text, vector, hybrid")
    if requested_mode == "auto":
        return "auto", "hybrid" if config.semantic.enabled else "text"
    return requested_mode, requested_mode


def _merge_results_by_id(
    results_by_id: dict[str, SearchResult],
    incoming: Sequence[SearchResult],
) -> None:
    for result in incoming:
        existing = results_by_id.get(result.id)
        if existing is None or _result_sort_key(result) < _result_sort_key(existing):
            results_by_id[result.id] = result


def _merged_results(
    results_by_id: Mapping[str, SearchResult], limit: int
) -> tuple[SearchResult, ...]:
    results = sorted(results_by_id.values(), key=_result_sort_key)
    return tuple(results[:limit])


def _result_sort_key(result: SearchResult) -> tuple[float, bool, str]:
    return (-result.score, result.related, result.id)


def _needs_fallback(results: Sequence[SearchResult], limit: int) -> bool:
    if not results:
        return True
    target = min(limit, 3)
    return _strong_result_count(results) < target


def _fallback_reason(results: Sequence[SearchResult], limit: int) -> str:
    if not results:
        return "no_results"
    return f"too_few_strong_results:{_strong_result_count(results)}/{min(limit, 3)}"


def _strong_result_count(results: Sequence[SearchResult]) -> int:
    return sum(1 for result in results if result.score >= STRONG_RESULT_SCORE)


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _query_tokens(query: str) -> list[str]:
    slug_spaced = re.sub(r"[-_/.:]+", " ", query)
    return [token.lower() for token in re.findall(r"\w+", slug_spaced, flags=re.UNICODE)]


def _normalize_query_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _expand_common_acronyms(tokens: Sequence[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        if token == "db":
            expanded.append("database")
        elif token == "fts":
            expanded.extend(("full", "text", "search"))
        else:
            expanded.append(token)
    return expanded


def _add_query_variant(variants: list[str], value: str, *, max_variants: int) -> None:
    if len(variants) >= max_variants:
        return
    cleaned = _collapse_whitespace(value)
    if not cleaned:
        return
    signature = cleaned.lower()
    if signature in {variant.lower() for variant in variants}:
        return
    variants.append(cleaned)


def _semantic_status(mode: str, enabled: bool, provider: Optional[str]) -> str:
    if not enabled:
        return "not_used"
    if provider:
        return "configured"
    if mode in SEMANTIC_SEARCH_MODES:
        return "missing_provider"
    return "not_used"


def _connect_index(config: MemoryConfig) -> sqlite3.Connection:
    if not config.index_file.exists():
        raise RetrievalIndexError(
            f"SQLite index not found at {config.index_file}; run `memora reindex` before searching."
        )

    connection = sqlite3.connect(config.index_file)
    connection.row_factory = sqlite3.Row
    try:
        existing = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
    except sqlite3.DatabaseError as exc:
        connection.close()
        raise RetrievalIndexError(
            f"SQLite index at {config.index_file} could not be read; run `memora reindex`."
        ) from exc

    missing = [name for name in REQUIRED_INDEX_OBJECTS if name not in existing]
    if missing:
        connection.close()
        raise RetrievalIndexError(
            "SQLite index is missing retrieval tables "
            f"({', '.join(missing)}); run `memora reindex --vault {config.vault_path}`."
        )
    return connection


def _provider_name(provider: EmbeddingProvider, config: MemoryConfig) -> Optional[str]:
    name = getattr(provider, "name", None)
    if isinstance(name, str) and name:
        return name
    return config.semantic.provider


def _primary_candidates(
    connection: sqlite3.Connection,
    fts_query: str,
    filters: SearchFilters,
    *,
    fetch_limit: int,
) -> list[_Candidate]:
    where_sql, parameters = _filter_sql(filters, table_alias="m")
    rows = connection.execute(
        f"""
        SELECT
            chunk_fts.id AS chunk_id,
            chunk_fts.document_id AS document_id,
            chunk_fts.chunk_type AS chunk_type,
            chunk_fts.text AS text,
            snippet(chunk_fts, 3, '[', ']', '...', 32) AS snippet,
            bm25(chunk_fts) AS fts_rank,
            d.path AS path,
            d.type AS document_type,
            d.status AS document_status,
            d.created_at AS created_at,
            d.updated_at AS updated_at,
            m.scope AS scope,
            m.project AS project,
            m.confidence AS confidence,
            m.valid_from AS valid_from,
            m.valid_to AS valid_to
        FROM chunk_fts
        JOIN documents d ON d.id = chunk_fts.document_id
        JOIN memories m ON m.document_id = chunk_fts.document_id
        WHERE chunk_fts MATCH ?
        {where_sql}
        ORDER BY fts_rank ASC, d.id ASC, chunk_fts.id ASC
        LIMIT ?
        """,
        (fts_query, *parameters, fetch_limit),
    ).fetchall()

    candidates_by_document: dict[str, _Candidate] = {}
    for row_order, row in enumerate(rows):
        document_id = str(row["document_id"])
        if document_id in candidates_by_document:
            continue
        candidates_by_document[document_id] = _candidate_from_row(
            row,
            fts_score=_fts_score(row_order),
            row_order=row_order,
            related=False,
        )
    return list(candidates_by_document.values())


def _semantic_candidates(
    connection: sqlite3.Connection,
    query: str,
    filters: SearchFilters,
    *,
    provider: EmbeddingProvider,
    fetch_limit: int,
    min_similarity: float,
) -> list[_Candidate]:
    _ensure_embeddings_schema(connection)
    query_vector = provider.embed([query])
    if len(query_vector) != 1:
        raise EmbeddingProviderError("embedding provider must return one vector for the query")

    rows = _semantic_rows(connection, filters, model=provider.model)
    chunk_vectors = _ensure_chunk_embeddings(connection, provider=provider, rows=rows)
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        vector = chunk_vectors.get(str(row["chunk_id"]))
        if vector is None:
            continue
        similarity = cosine_similarity(query_vector[0], vector)
        if similarity <= 0.0 and min_similarity <= 0.0:
            continue
        if similarity < min_similarity:
            continue
        scored.append((similarity, row))

    scored.sort(key=lambda item: (-item[0], str(item[1]["document_id"]), str(item[1]["chunk_id"])))
    candidates_by_document: dict[str, _Candidate] = {}
    for row_order, (similarity, row) in enumerate(scored):
        document_id = str(row["document_id"])
        if document_id in candidates_by_document:
            continue
        candidates_by_document[document_id] = _candidate_from_row(
            row,
            fts_score=0.0,
            row_order=row_order,
            related=False,
            snippet=_plain_snippet(str(row["text"])),
            semantic_score=10.0 * similarity,
            semantic_similarity=similarity,
        )
        if len(candidates_by_document) >= fetch_limit:
            break
    return list(candidates_by_document.values())


def _semantic_rows(
    connection: sqlite3.Connection,
    filters: SearchFilters,
    *,
    model: str,
) -> list[sqlite3.Row]:
    where_sql, parameters = _filter_sql(filters, table_alias="m")
    return connection.execute(
        f"""
        SELECT
            c.id AS chunk_id,
            c.document_id AS document_id,
            c.chunk_type AS chunk_type,
            c.text AS text,
            c.content_hash AS content_hash,
            d.path AS path,
            d.type AS document_type,
            d.status AS document_status,
            d.created_at AS created_at,
            d.updated_at AS updated_at,
            m.scope AS scope,
            m.project AS project,
            m.confidence AS confidence,
            m.valid_from AS valid_from,
            m.valid_to AS valid_to,
            e.vector AS embedding_vector,
            e.content_hash AS embedding_content_hash
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN memories m ON m.document_id = c.document_id
        LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ?
        WHERE 1 = 1
        {where_sql}
        ORDER BY d.id ASC, c.id ASC
        """,
        (model, *parameters),
    ).fetchall()


def _ensure_chunk_embeddings(
    connection: sqlite3.Connection,
    *,
    provider: EmbeddingProvider,
    rows: Sequence[sqlite3.Row],
) -> dict[str, list[float]]:
    vectors_by_chunk: dict[str, list[float]] = {}
    missing_rows: list[sqlite3.Row] = []

    for row in rows:
        chunk_id = str(row["chunk_id"])
        vector_payload = row["embedding_vector"]
        embedding_hash = row["embedding_content_hash"]
        if vector_payload is not None and embedding_hash == row["content_hash"]:
            try:
                vectors_by_chunk[chunk_id] = deserialize_vector(str(vector_payload))
                continue
            except EmbeddingProviderError:
                pass
        missing_rows.append(row)

    if not missing_rows:
        return vectors_by_chunk

    generated = provider.embed([str(row["text"]) for row in missing_rows])
    if len(generated) != len(missing_rows):
        raise EmbeddingProviderError(
            f"embedding provider returned {len(generated)} vectors for {len(missing_rows)} chunks"
        )

    for row, vector in zip(missing_rows, generated):
        chunk_id = str(row["chunk_id"])
        vectors_by_chunk[chunk_id] = list(vector)
        connection.execute(
            """
            INSERT INTO embeddings (chunk_id, model, vector, content_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chunk_id, model) DO UPDATE SET
                vector = excluded.vector,
                content_hash = excluded.content_hash,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                chunk_id,
                provider.model,
                serialize_vector(vector),
                str(row["content_hash"]),
            ),
        )
    connection.commit()
    return vectors_by_chunk


def _ensure_embeddings_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
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
        """
    )


def _merge_candidates(
    base: Sequence[_Candidate],
    incoming: Sequence[_Candidate],
) -> list[_Candidate]:
    candidates_by_document: dict[str, _Candidate] = {}
    for candidate in (*base, *incoming):
        existing = candidates_by_document.get(candidate.document_id)
        if existing is None:
            candidates_by_document[candidate.document_id] = candidate
            continue

        preferred = candidate if existing.related and not candidate.related else existing
        candidates_by_document[candidate.document_id] = replace(
            preferred,
            fts_score=max(existing.fts_score, candidate.fts_score),
            fts_rank=existing.fts_rank
            if existing.fts_score >= candidate.fts_score
            else candidate.fts_rank,
            row_order=min(existing.row_order, candidate.row_order),
            semantic_score=max(existing.semantic_score, candidate.semantic_score),
            semantic_similarity=_max_optional(
                existing.semantic_similarity, candidate.semantic_similarity
            ),
            related=existing.related and candidate.related,
        )

    return sorted(candidates_by_document.values(), key=lambda candidate: candidate.row_order)


def _related_candidates(
    connection: sqlite3.Connection,
    primary: Sequence[_Candidate],
    filters: SearchFilters,
    *,
    max_related: int,
) -> list[_Candidate]:
    primary_ids = [candidate.document_id for candidate in primary]
    placeholders = ", ".join("?" for _ in primary_ids)
    rows = connection.execute(
        f"""
        SELECT from_id, to_id, relation, confidence
        FROM links
        WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
        ORDER BY from_id ASC, to_id ASC, relation ASC
        """,
        (*primary_ids, *primary_ids),
    ).fetchall()

    primary_set = set(primary_ids)
    related: list[_Candidate] = []
    seen = set(primary_ids)
    for row in rows:
        from_id = str(row["from_id"])
        to_id = str(row["to_id"])
        if from_id in primary_set:
            neighbor_id = to_id
            related_to = from_id
            direction = "outgoing"
        else:
            neighbor_id = from_id
            related_to = to_id
            direction = "incoming"

        if neighbor_id in seen:
            continue
        candidate = _document_candidate(connection, neighbor_id, filters)
        if candidate is None:
            continue
        related.append(
            _Candidate(
                **{
                    **candidate.__dict__,
                    "related": True,
                    "relation": str(row["relation"]),
                    "related_to": related_to,
                    "relation_direction": direction,
                    "relation_confidence": _optional_float(row["confidence"]),
                    "fts_score": 0.0,
                    "fts_rank": 0.0,
                    "row_order": len(primary) + len(related),
                }
            )
        )
        seen.add(neighbor_id)
        if len(related) >= max_related:
            break
    return related


def _document_candidate(
    connection: sqlite3.Connection,
    document_id: str,
    filters: SearchFilters,
) -> Optional[_Candidate]:
    where_sql, parameters = _filter_sql(filters, table_alias="m")
    row = connection.execute(
        f"""
        SELECT
            c.id AS chunk_id,
            c.document_id AS document_id,
            c.chunk_type AS chunk_type,
            c.text AS text,
            d.path AS path,
            d.type AS document_type,
            d.status AS document_status,
            d.created_at AS created_at,
            d.updated_at AS updated_at,
            m.scope AS scope,
            m.project AS project,
            m.confidence AS confidence,
            m.valid_from AS valid_from,
            m.valid_to AS valid_to
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN memories m ON m.document_id = c.document_id
        WHERE c.document_id = ?
        {where_sql}
        ORDER BY CASE WHEN c.chunk_type = 'body' THEN 0 ELSE 1 END, c.id ASC
        LIMIT 1
        """,
        (document_id, *parameters),
    ).fetchone()
    if row is None:
        return None
    return _candidate_from_row(
        row,
        fts_score=0.0,
        row_order=0,
        related=True,
        snippet=_plain_snippet(str(row["text"])),
    )


def _candidate_from_row(
    row: sqlite3.Row,
    *,
    fts_score: float,
    row_order: int,
    related: bool,
    snippet: Optional[str] = None,
    semantic_score: float = 0.0,
    semantic_similarity: Optional[float] = None,
) -> _Candidate:
    return _Candidate(
        document_id=str(row["document_id"]),
        chunk_id=str(row["chunk_id"]),
        chunk_type=str(row["chunk_type"]),
        text=str(row["text"]),
        snippet=_clean_snippet(snippet if snippet is not None else str(row["snippet"])),
        path=str(row["path"]),
        memory_type=str(row["document_type"]),
        status=str(row["document_status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        scope=str(row["scope"]),
        project=_optional_string(row["project"]),
        confidence=_optional_float(row["confidence"]),
        valid_from=_optional_string(row["valid_from"]),
        valid_to=_optional_string(row["valid_to"]),
        fts_score=fts_score,
        fts_rank=float(row["fts_rank"]) if "fts_rank" in row.keys() else 0.0,
        row_order=row_order,
        semantic_score=semantic_score,
        semantic_similarity=semantic_similarity,
        related=related,
    )


def _rank_candidate(
    candidate: _Candidate,
    reference_time: Optional[datetime],
    connected_primary_ids: set[str],
    superseded_ids: set[str],
) -> SearchResult:
    graph_neighbor_boost = 0.75 if candidate.related else 0.0
    if not candidate.related and candidate.document_id in connected_primary_ids:
        graph_neighbor_boost = 0.30

    confidence = candidate.confidence if candidate.confidence is not None else 1.0
    confidence_boost = max(0.0, min(confidence, 1.0)) * 0.50
    recency_boost = _recency_boost(candidate.updated_at, reference_time)
    rating_boost = 0.0
    stale_penalty = _stale_penalty(candidate, reference_time)
    superseded_penalty = 1.50 if candidate.status == LifecycleStatus.SUPERSEDED.value else 0.0
    if candidate.document_id in superseded_ids:
        superseded_penalty = max(superseded_penalty, 1.50)

    breakdown = {
        "fts_score": candidate.fts_score,
        "graph_neighbor_boost": graph_neighbor_boost,
        "memory_type_boost": MEMORY_TYPE_BOOSTS.get(candidate.memory_type, 0.0),
        "status_boost": STATUS_BOOSTS.get(candidate.status, 0.0),
        "confidence_boost": confidence_boost,
        "recency_boost": recency_boost,
        "rating_boost": rating_boost,
        "stale_penalty": stale_penalty,
        "superseded_penalty": superseded_penalty,
    }
    if candidate.semantic_score > 0.0:
        breakdown["semantic_score"] = candidate.semantic_score
    score = (
        breakdown["fts_score"]
        + breakdown.get("semantic_score", 0.0)
        + breakdown["graph_neighbor_boost"]
        + breakdown["memory_type_boost"]
        + breakdown["status_boost"]
        + breakdown["confidence_boost"]
        + breakdown["recency_boost"]
        + breakdown["rating_boost"]
        - breakdown["stale_penalty"]
        - breakdown["superseded_penalty"]
    )

    metadata: dict[str, Any] = {
        "type": candidate.memory_type,
        "status": candidate.status,
        "scope": candidate.scope,
        "project": candidate.project,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "valid_from": candidate.valid_from,
        "valid_to": candidate.valid_to,
        "confidence": candidate.confidence,
        "chunk_id": candidate.chunk_id,
        "chunk_type": candidate.chunk_type,
        "fts_rank": candidate.fts_rank,
        "related": candidate.related,
    }
    if candidate.semantic_similarity is not None:
        metadata["semantic_similarity"] = candidate.semantic_similarity
    if candidate.related:
        metadata.update(
            {
                "relation": candidate.relation,
                "related_to": candidate.related_to,
                "relation_direction": candidate.relation_direction,
                "relation_confidence": candidate.relation_confidence,
            }
        )

    return SearchResult(
        id=candidate.document_id,
        score=score,
        snippet=candidate.snippet,
        path=candidate.path,
        metadata=metadata,
        score_breakdown=breakdown,
        related=candidate.related,
    )


def _safety_annotated_results(
    config: MemoryConfig,
    results: Sequence[SearchResult],
    *,
    filter_unsafe: bool,
) -> tuple[SearchResult, ...]:
    selected: list[SearchResult] = []
    for result in results:
        risk_flags = _risk_flags_for_path(config, result.path)
        metadata = dict(result.metadata)
        if risk_flags:
            metadata["risk_flags"] = list(risk_flags)
        annotated = replace(result, metadata=metadata)
        if filter_unsafe and has_unsafe_recall_risk(risk_flags):
            continue
        selected.append(annotated)
    return tuple(selected)


def _risk_flags_for_path(config: MemoryConfig, relative_path: str) -> tuple[str, ...]:
    path = config.vault_path / relative_path
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ()
    frontmatter = _frontmatter_mapping(text)
    flags = list(normalize_risk_flags(frontmatter.get("risk_flags")))
    flags.extend(scan_metadata(frontmatter).risk_flags)
    flags.extend(scan_text(_body_text(text), field="memory").risk_flags)
    return normalize_risk_flags(flags)


def _body_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return normalized
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        return normalized
    return parts[1]


def _frontmatter_mapping(text: str) -> Mapping[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    payload = yaml.safe_load(parts[0][4:]) or {}
    return payload if isinstance(payload, Mapping) else {}


def _filter_sql(filters: SearchFilters, *, table_alias: str) -> tuple[str, tuple[Any, ...]]:
    conditions: list[str] = []
    parameters: list[Any] = []

    if filters.project is not None:
        conditions.append(f"{table_alias}.project = ?")
        parameters.append(filters.project)
    if filters.memory_type is not None:
        conditions.append(f"{table_alias}.type = ?")
        parameters.append(filters.memory_type)
    if filters.status is not None:
        conditions.append(f"{table_alias}.status = ?")
        parameters.append(filters.status)
    else:
        placeholders = ", ".join("?" for _ in DEFAULT_STATUSES)
        conditions.append(f"{table_alias}.status IN ({placeholders})")
        parameters.extend(DEFAULT_STATUSES)
    if filters.scope is not None:
        conditions.append(f"{table_alias}.scope = ?")
        parameters.append(filters.scope)
    if filters.created_after is not None:
        conditions.append("d.created_at >= ?")
        parameters.append(filters.created_after)
    if filters.created_before is not None:
        conditions.append("d.created_at <= ?")
        parameters.append(filters.created_before)
    if filters.updated_after is not None:
        conditions.append("d.updated_at >= ?")
        parameters.append(filters.updated_after)
    if filters.updated_before is not None:
        conditions.append("d.updated_at <= ?")
        parameters.append(filters.updated_before)
    if filters.valid_from is not None:
        conditions.append(f"{table_alias}.valid_from >= ?")
        parameters.append(filters.valid_from)
    if filters.valid_to is not None:
        conditions.append(f"{table_alias}.valid_to <= ?")
        parameters.append(filters.valid_to)

    if not conditions:
        return "", ()
    return "AND " + " AND ".join(conditions), tuple(parameters)


def _connected_document_ids(connection: sqlite3.Connection, document_ids: set[str]) -> set[str]:
    if not document_ids:
        return set()
    placeholders = ", ".join("?" for _ in document_ids)
    rows = connection.execute(
        f"""
        SELECT from_id, to_id
        FROM links
        WHERE from_id IN ({placeholders}) AND to_id IN ({placeholders})
        """,
        (*document_ids, *document_ids),
    ).fetchall()
    connected: set[str] = set()
    for row in rows:
        connected.add(str(row["from_id"]))
        connected.add(str(row["to_id"]))
    return connected


def _superseded_document_ids(connection: sqlite3.Connection, document_ids: set[str]) -> set[str]:
    if not document_ids:
        return set()
    placeholders = ", ".join("?" for _ in document_ids)
    rows = connection.execute(
        f"""
        SELECT to_id
        FROM links
        WHERE relation = ? AND to_id IN ({placeholders})
        """,
        (RelationType.SUPERSEDES.value, *document_ids),
    ).fetchall()
    return {str(row["to_id"]) for row in rows}


def _reference_time(candidates: Sequence[_Candidate]) -> Optional[datetime]:
    parsed = [_parse_datetime(candidate.updated_at) for candidate in candidates]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _recency_boost(updated_at: str, reference_time: Optional[datetime]) -> float:
    updated = _parse_datetime(updated_at)
    if updated is None or reference_time is None:
        return 0.0
    age_days = max(0.0, (reference_time - updated).total_seconds() / 86400)
    return max(0.0, 0.50 - (min(age_days, 365.0) / 365.0) * 0.50)


def _stale_penalty(candidate: _Candidate, reference_time: Optional[datetime]) -> float:
    if candidate.status == LifecycleStatus.STALE.value:
        return 1.20
    if candidate.valid_to and reference_time:
        valid_to = _parse_date(candidate.valid_to)
        if valid_to and valid_to < reference_time.date():
            return 1.20
    return 0.0


def _fts_score(row_order: int) -> float:
    return 10.0 / (1.0 + (row_order * 0.25))


def _to_fts_query(query: str) -> str:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("search query must include at least one searchable term")
    return " ".join(f'"{token}"' for token in tokens)


def _plain_snippet(text: str, *, max_length: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[: max_length - 3].rstrip()}..."


def _clean_snippet(text: str) -> str:
    return _plain_snippet(text.replace("\n", " "))


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _max_optional(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _optional_enum_value(enum_type: Any, value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return enum_type(value).value


def _optional_datetime_filter(value: Any, *, end_of_day: bool = False) -> Optional[str]:
    cleaned = _optional_string(value)
    if cleaned is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        suffix = "T23:59:59" if end_of_day else "T00:00:00"
        return f"{cleaned}{suffix}"
    datetime.fromisoformat(cleaned)
    return cleaned


def _optional_date_filter(value: Any) -> Optional[str]:
    cleaned = _optional_string(value)
    if cleaned is None:
        return None
    date.fromisoformat(cleaned)
    return cleaned


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "QueryPlan",
    "RetrievalIndexError",
    "SearchAttempt",
    "SearchFilters",
    "SearchMode",
    "SearchResponse",
    "SearchResult",
    "plan_query_variants",
    "search_memory",
]
