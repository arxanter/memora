"""MCP tool handlers and optional FastMCP server registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Union

from agent_memory.brief import brief_memory
from agent_memory.config import (
    AgentPolicyConfig,
    AgentTrustLevel,
    ConfigError,
    MemoryConfig,
    TaskRecallPolicyConfig,
    load_config,
)
from agent_memory.freshness import refresh_index_if_needed
from agent_memory.lifecycle import (
    curation_plan,
    mark_status,
    reject_memory,
    review_queue,
    supersede_memory,
)
from agent_memory.profile import build_context_profile_payload, build_profile as build_profile_service
from agent_memory.recall import explain_recall, recall_memory
from agent_memory.recall_policy import should_recall
from agent_memory.retrieval import RetrievalIndexError, SearchFilters, search_memory
from agent_memory.schema import (
    AuthorKind,
    LifecycleStatus,
    MemoryScope,
    MemoryType,
    SourceRef,
)
from agent_memory.session import normalize_loaded_ids, normalize_session_recall_state, session_trace
from agent_memory.sources import lookup_source, save_source_material, save_source_with_memories
from agent_memory.ux import inspect_memory
from agent_memory.vault import placeholder_result, remember_memory

try:  # pragma: no cover - exercised only when the optional MCP extra is installed.
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]

JsonPayload = dict[str, Any]
PathLike = Union[Path, str]
SOURCE_INBOX_SUFFIXES = {".md", ".markdown", ".txt"}


def remember_tool(memory: Mapping[str, Any], *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Create a pending agent-authored memory using the shared vault service."""

    try:
        config = load_config(_vault_from(memory, vault))
        memory_type = MemoryType(memory.get("type", MemoryType.FACT.value))
        text = _memory_text(memory)
        confidence = float(memory.get("confidence", config.agent_policy.min_pending_confidence))
        source = _memory_source(memory)
        author_name = _optional_string(memory.get("author_name")) or "MCP agent"
        selected_status = _agent_memory_status(memory, config.agent_policy, confidence)
        result = remember_memory(
            config,
            memory_type=memory_type,
            text=text,
            scope=_optional_enum(MemoryScope, memory.get("scope")),
            project=_optional_string(memory.get("project")),
            status=selected_status,
            tags=_string_list(memory.get("tags", ())),
            author_kind=AuthorKind.AGENT,
            author_name=author_name,
            source=source,
            confidence=confidence,
        )
        payload = result.to_dict()
        payload.update(
            {
                "tool": "remember",
                "review_required": payload["status"] == LifecycleStatus.PENDING.value,
                "author": {"kind": AuthorKind.AGENT.value, "name": author_name},
                "confidence": confidence,
                "policy": _agent_policy_payload(
                    config.agent_policy,
                    selected_status=result.status,
                    confidence=confidence,
                    explicit_user_save=_explicit_user_save(memory),
                ),
                "citations": [_citation(payload["id"], payload["relative_path"])],
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="remember_failed")


def save_source_tool(source: Mapping[str, Any], *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Save raw source material and optional extract under Sources/."""

    try:
        config = load_config(_vault_from(source, vault))
        payload = save_source_material(
            config,
            title=_optional_string(source.get("title")),
            url=_optional_string(source.get("url")),
            content=_optional_string(source.get("content") or source.get("raw") or source.get("markdown")),
            extract=_optional_string(source.get("extract") or source.get("summary")),
            project=_optional_string(source.get("project")),
            tags=_string_list(source.get("tags", ())),
            channel=_optional_string(source.get("channel")),
            source_quality=_optional_string(source.get("source_quality")),
            sensitivity=_optional_string(source.get("sensitivity")),
            origin=source.get("origin") if isinstance(source.get("origin"), Mapping) else None,
            slug=_optional_string(source.get("slug")),
        ).to_dict()
        payload.update(
            {
                "tool": "save_source",
                "next_steps": [
                    "Review source.md and extract.md in the vault.",
                    "Call remember(memory) for durable facts, decisions, preferences, project_context, or tasks.",
                    "Keep agent-created memories pending until reviewed.",
                ],
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="save_source_failed", tool="save_source")


def save_source_with_memories_tool(
    source: Mapping[str, Any],
    memories: list[Mapping[str, Any]],
    author_name: Optional[str] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Save source/extract material and create linked pending atomic memories."""

    try:
        config = load_config(_vault_from(source, vault))
        payload = save_source_with_memories(
            config,
            source=source,
            memories=memories,
            author_name=_optional_string(author_name) or "MCP agent",
        ).to_dict()
        payload.update(
            {
                "tool": "save_source_with_memories",
                "policy": {
                    "trust_level": _trust_level(config.agent_policy),
                    "require_review_for_source_extracts": config.agent_policy.require_review_for_source_extracts,
                    "min_active_confidence": config.agent_policy.min_active_confidence,
                    "min_pending_confidence": config.agent_policy.min_pending_confidence,
                },
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="save_source_with_memories_failed",
            tool="save_source_with_memories",
        )


def lookup_source_tool(
    source_id: str,
    query: Optional[str] = None,
    budget: int = 800,
    session_id: Optional[str] = None,
    loaded_source_ids: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Return compact read-only evidence for a saved source directory."""

    try:
        config = load_config(vault)
        return lookup_source(
            config,
            source_id,
            query=query,
            budget=budget,
            session_id=session_id,
            loaded_source_ids=loaded_source_ids,
        )
    except Exception as exc:
        payload = _error_payload(
            exc,
            code="lookup_source_failed",
            tool="lookup_source",
            source_id=source_id,
            query=query,
            budget=budget,
        )
        payload.update({"implemented": True, "chunks": []})
        return payload


def ingest_url_tool(
    url: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    extract: Optional[str] = None,
    project: Optional[str] = None,
    tags: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Save URL source material; the agent is responsible for fetching/analyzing content."""

    if not _optional_string(url):
        return _error_payload(ValueError("url must not be empty"), code="invalid_url", tool="ingest_url")

    return save_source_tool(
        {
            "url": url,
            "title": title,
            "content": content,
            "extract": extract,
            "project": project,
            "tags": tags or [],
            "channel": "url",
            "source_quality": "agent_fetched" if _optional_string(content) else "unknown",
            "origin": {"provider": "web", "url": url},
        },
        vault=vault,
    ) | {"tool": "ingest_url"}


def import_source_tool(
    path: PathLike,
    title: Optional[str] = None,
    extract_file: Optional[PathLike] = None,
    project: Optional[str] = None,
    channel: str = "file",
    source_quality: str = "imported_export",
    sensitivity: str = "normal",
    tags: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Save a Markdown/text file as source material under Sources/."""

    try:
        config = load_config(vault)
        source_path = _expanded_path(path)
        extract = _read_optional_text_file(extract_file)
        payload = _save_file_source(
            config,
            source_path,
            title=title,
            extract=extract,
            project=project,
            tags=tags or [],
            channel=channel,
            source_quality=source_quality,
            sensitivity=sensitivity,
        )
        payload.update({"tool": "import_source", "command": "import-source"})
        return payload
    except Exception as exc:
        return _error_payload(exc, code="import_source_failed", tool="import_source")


def import_source_inbox_tool(
    path: PathLike,
    project: Optional[str] = None,
    channel: str = "web_clipper",
    source_quality: str = "imported_export",
    sensitivity: str = "normal",
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Import Markdown/text files from a source inbox directory."""

    try:
        config = load_config(vault)
        inbox = _expanded_path(path)
        if not inbox.is_dir():
            raise ValueError(f"source inbox is not a directory: {inbox}")
        candidates = _source_inbox_files(inbox)
        if dry_run:
            payload = _source_inbox_plan_payload(inbox, candidates, dry_run=True)
        else:
            sources = [
                _save_file_source(
                    config,
                    candidate,
                    title=candidate.stem,
                    project=project,
                    tags=tags or [],
                    channel=channel,
                    source_quality=source_quality,
                    sensitivity=sensitivity,
                )
                for candidate in candidates
            ]
            payload = {
                "ok": True,
                "implemented": True,
                "tool": "import_source_inbox",
                "command": "import-source-inbox",
                "dry_run": False,
                "inbox_path": str(inbox),
                "source_count": len(sources),
                "sources": sources,
            }
        payload.update({"tool": "import_source_inbox"})
        return payload
    except Exception as exc:
        return _error_payload(exc, code="import_source_inbox_failed", tool="import_source_inbox")


def import_session_tool(
    path: PathLike,
    summary: Optional[str] = None,
    summary_file: Optional[PathLike] = None,
    remember_summary: bool = False,
    session_format: str = "text",
    project: Optional[str] = None,
    sensitivity: str = "normal",
    tags: Optional[list[str]] = None,
    confidence: float = 0.75,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Save an AI-agent session transcript and optional pending summary memory."""

    try:
        if remember_summary and not (_optional_string(summary) or summary_file is not None):
            raise ValueError("remember_summary requires summary or summary_file")
        config = load_config(vault)
        transcript_path = _expanded_path(path)
        transcript = transcript_path.read_text(encoding="utf-8")
        summary_text = _optional_string(summary) or _read_optional_text_file(summary_file)
        selected_tags = [*(tags or []), "ai-session"]
        saved_source = save_source_material(
            config,
            title=transcript_path.stem,
            content=transcript,
            extract=summary_text,
            project=project,
            tags=selected_tags,
            channel="ai_session",
            source_quality="imported_export",
            sensitivity=sensitivity,
            origin={
                "provider": "file",
                "file_name": transcript_path.name,
                "path": str(transcript_path),
                "format": session_format,
            },
        )
        payload: dict[str, Any] = {
            "ok": True,
            "implemented": True,
            "tool": "import_session",
            "command": "import-session",
            "source": saved_source.to_dict(),
            "memory": None,
        }
        if remember_summary:
            source_path = saved_source.relative_extract_path or saved_source.relative_source_path
            memory = remember_memory(
                config,
                memory_type=MemoryType.CONVERSATION_SUMMARY,
                text=summary_text or "",
                scope=MemoryScope.PROJECT if project else None,
                project=project,
                status=LifecycleStatus.PENDING,
                tags=selected_tags,
                author_kind=AuthorKind.AGENT,
                author_name="session import",
                source={
                    "path": source_path.as_posix(),
                    "title": saved_source.title,
                    "source_id": saved_source.source_id,
                },
                confidence=confidence,
                risk_flags=saved_source.risk_flags,
            )
            payload["memory"] = memory.to_dict()
            payload["review_required"] = memory.status == LifecycleStatus.PENDING
        return payload
    except Exception as exc:
        return _error_payload(exc, code="import_session_failed", tool="import_session")


def search_tool(
    query: str,
    filters: Optional[Mapping[str, Any]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Search indexed memory using the shared Stage 5 retrieval service."""

    raw_filters = _filters(filters)
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    mode = str(raw_filters.pop("mode", "auto"))
    limit = int(raw_filters.pop("limit", 10))
    try:
        config = load_config(vault)
        freshness = _refresh_index_for_query(config, before="search")
        payload = search_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            include_related=include_related,
            limit=limit,
            semantic=None if semantic is None else _bool(semantic),
            mode=mode,
        ).to_dict()
        payload.update({"tool": "search", "freshness": freshness})
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "search_failed",
            tool="search",
            query=query,
            filters=raw_filters,
        )


def recall_tool(
    query: str,
    budget: int = 1200,
    filters: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    loaded_memory_ids: Optional[list[str]] = None,
    loaded_source_ids: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Recall ranked memory chunks packed under a strict token budget."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="recall")

    raw_filters = _filters(filters)
    session_inputs = _session_inputs(
        raw_filters,
        session_id=session_id,
        loaded_memory_ids=loaded_memory_ids,
        loaded_source_ids=loaded_source_ids,
    )
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    mode = str(raw_filters.pop("mode", "auto"))
    try:
        config = load_config(vault)
        freshness = _refresh_index_for_query(config, before="recall")
        payload = recall_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            budget=selected_budget,
            include_related=include_related,
            semantic=None if semantic is None else _bool(semantic),
            mode=mode,
            **session_inputs,
        ).to_dict()
        payload.update({"tool": "recall", "freshness": freshness})
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "recall_failed",
            tool="recall",
            query=query,
            budget=selected_budget,
            filters=_filters(filters),
        )


def brief_tool(
    query: str,
    budget: int = 1200,
    filters: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    loaded_memory_ids: Optional[list[str]] = None,
    loaded_source_ids: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Generate a citation-preserving memory brief under a strict budget."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="brief")

    raw_filters = _filters(filters)
    session_inputs = _session_inputs(
        raw_filters,
        session_id=session_id,
        loaded_memory_ids=loaded_memory_ids,
        loaded_source_ids=loaded_source_ids,
    )
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    mode = str(raw_filters.pop("mode", "auto"))
    try:
        config = load_config(vault)
        freshness = _refresh_index_for_query(config, before="recall")
        payload = brief_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            budget=selected_budget,
            include_related=include_related,
            semantic=None if semantic is None else _bool(semantic),
            mode=mode,
            **session_inputs,
        ).to_dict()
        payload.update({"tool": "brief", "freshness": freshness})
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "brief_failed",
            tool="brief",
            query=query,
            budget=selected_budget,
            filters=_filters(filters),
        )


def build_profile_tool(
    profile_type: str = "user",
    project: Optional[str] = None,
    budget: Optional[int] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Write a deterministic generated profile under Profiles/."""

    try:
        config = load_config(vault)
        return build_profile_service(
            config,
            profile_type=profile_type,
            project=project,
            budget=budget,
        ).to_dict()
    except Exception as exc:
        return _error_payload(
            exc,
            code="build_profile_failed",
            tool="build_profile",
            profile_type=profile_type,
            project=project,
            budget=budget,
        )


def should_recall_tool(message: str) -> JsonPayload:
    """Classify whether a user request should be enriched with memory."""

    payload = should_recall(message).to_dict()
    payload.update({"tool": "should_recall"})
    return payload


def build_context_tool(
    task: str,
    budget: int = 1200,
    filters: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    loaded_memory_ids: Optional[list[str]] = None,
    loaded_source_ids: Optional[list[str]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Build agent context by applying recall policy before generating a brief."""

    config = _optional_config(vault)
    agent_policy = config.agent_policy if config else AgentPolicyConfig()
    raw_filters = _filters(filters)
    session_inputs = _session_inputs(
        raw_filters,
        session_id=session_id,
        loaded_memory_ids=loaded_memory_ids,
        loaded_source_ids=loaded_source_ids,
    )
    session_state = normalize_session_recall_state(**session_inputs)
    include_profile_override = raw_filters.pop("include_profile", None)
    task_class, task_policy = _resolve_task_recall_policy(config, raw_filters.pop("task_class", None))
    if task_policy.include_related and "include_related" not in raw_filters:
        raw_filters["include_related"] = True
    policy = should_recall(task, aliases=agent_policy.aliases).to_dict()
    try:
        selected_budget = _build_context_budget(budget, task_policy, agent_policy)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="build_context", task=task, policy=policy)

    profile_requested, profile_request_sources = _resolve_build_context_profile_request(
        config,
        task_policy,
        include_profile_override,
    )
    profile_project = _optional_string(raw_filters.get("project"))

    if not policy["should_recall"]:
        session_payload = session_trace(session_state)
        profile_trace = _policy_skipped_profile_payload(
            config,
            task_policy,
            requested=profile_requested,
            request_sources=profile_request_sources,
            project=profile_project,
            task_budget=selected_budget,
        )
        payload = {
            "ok": True,
            "implemented": True,
            "tool": "build_context",
            "task": task,
            "budget": selected_budget,
            "memory_needed": False,
            "policy": policy,
            "task_class": task_class,
            "profile": profile_trace,
            "trace": _build_context_trace(
                policy,
                task_class=task_class,
                task_policy=task_policy,
                profile=profile_trace,
                task_budget=selected_budget,
                session=session_payload,
                empty_reason="policy_skipped",
            ),
            "markdown": "",
            "brief": None,
            "citations": [],
        }
        if session_payload:
            payload["session"] = session_payload
        return payload

    profile_trace = _build_context_profile_trace(
        config,
        task_policy,
        requested=profile_requested,
        request_sources=profile_request_sources,
        project=profile_project,
        task_budget=selected_budget,
    )
    brief_payload = brief_tool(
        str(policy["query"]),
        selected_budget,
        raw_filters,
        **session_inputs,
        vault=vault,
    )
    if not brief_payload.get("ok"):
        brief_payload.update(
            {
                "tool": "build_context",
                "task": task,
                "memory_needed": True,
                "policy": policy,
                "task_class": task_class,
                "profile": profile_trace,
                "trace": _build_context_trace(
                    policy,
                    task_class=task_class,
                    task_policy=task_policy,
                    profile=profile_trace,
                    task_budget=selected_budget,
                    session=brief_payload.get("session") or session_trace(session_state),
                    empty_reason="brief_failed",
                ),
            }
        )
        return brief_payload

    retrieval_trace = brief_payload.get("retrieval")
    chunk_count = int(brief_payload.get("recall", {}).get("chunk_count", 0))
    empty_reason = None
    if chunk_count == 0 and not (
        isinstance(retrieval_trace, Mapping) and retrieval_trace.get("empty_reason")
    ):
        empty_reason = "no_selected_chunks"

    return {
        "ok": True,
        "implemented": True,
        "tool": "build_context",
        "task": task,
        "budget": selected_budget,
        "memory_needed": True,
        "policy": policy,
        "task_class": task_class,
        "profile": profile_trace,
        "trace": _build_context_trace(
            policy,
            task_class=task_class,
            task_policy=task_policy,
            profile=profile_trace,
            task_budget=selected_budget,
            retrieval=retrieval_trace,
            freshness=brief_payload.get("freshness"),
            session=brief_payload.get("session"),
            selected_count=chunk_count,
            empty_reason=empty_reason,
        ),
        "markdown": _compose_context_markdown(profile_trace, brief_payload["markdown"]),
        "brief": brief_payload,
        "citations": [*profile_trace.get("citations", []), *brief_payload["citations"]],
    } | ({"session": brief_payload["session"]} if brief_payload.get("session") else {})


def inspect_tool(memory_id: str, *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Inspect one Markdown memory by id using the Stage 1 validator."""

    try:
        config = load_config(vault)
        payload = inspect_memory(config, memory_id)
        payload.update({"tool": "inspect"})
        return payload
    except ValueError as exc:
        return {
            "ok": False,
            "tool": "inspect",
            "id": memory_id,
            "found": False,
            "error": {
                "code": "memory_not_found",
                "message": str(exc),
            },
            "citations": [],
        }
    except Exception as exc:
        return _error_payload(exc, code="inspect_failed", tool="inspect", id=memory_id)


def explain_recall_tool(
    query: str,
    budget: int = 1200,
    filters: Optional[Mapping[str, Any]] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Explain deterministic recall selection and packing decisions."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="explain_recall")

    raw_filters = _filters(filters)
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    mode = str(raw_filters.pop("mode", "auto"))
    try:
        config = load_config(vault)
        payload = explain_recall(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            budget=selected_budget,
            include_related=include_related,
            semantic=None if semantic is None else _bool(semantic),
            mode=mode,
        ).to_dict()
        payload.update({"tool": "explain_recall"})
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "explain_recall_failed",
            tool="explain_recall",
            query=query,
            budget=selected_budget,
            filters=_filters(filters),
        )


def mark_status_tool(memory_id: str, status: str, *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Mutate a memory lifecycle status using the shared lifecycle service."""

    try:
        selected_status = LifecycleStatus(status).value
    except Exception as exc:
        return _error_payload(exc, code="invalid_status", tool="mark_status", id=memory_id)

    try:
        config = load_config(vault)
        payload = mark_status(config, memory_id, selected_status).to_dict()
        payload.update(
            {
                "tool": "mark_status",
                "id": memory_id,
                "status": selected_status,
                "mutated": payload["mutation_count"] > 0,
                "policy": _lifecycle_policy_payload(config.agent_policy, selected_status),
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="mark_status_failed", tool="mark_status", id=memory_id)


def review_tool(*, vault: Optional[PathLike] = None) -> JsonPayload:
    """Return pending agent-generated memories awaiting review."""

    try:
        config = load_config(vault)
        payload = review_queue(config).to_dict()
        payload.update(
            {
                "tool": "review",
                "policy": {
                    "trust_level": _trust_level(config.agent_policy),
                    "autonomous_lifecycle": config.agent_policy.autonomous_lifecycle,
                    "min_active_confidence": config.agent_policy.min_active_confidence,
                },
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="review_failed", tool="review")


def curate_tool(
    project: Optional[str] = None,
    source: Optional[str] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Return read-only curation proposals for pending agent memories."""

    try:
        config = load_config(vault)
        payload = curation_plan(config, project=project, source=source)
        payload.setdefault("tool", "curate")
        return payload
    except Exception as exc:
        return _error_payload(exc, code="curate_failed", tool="curate")


def approve_tool(memory_id: str, reason: Optional[str] = None, *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Approve a pending memory by marking it active."""

    try:
        config = load_config(vault)
        payload = mark_status(config, memory_id, LifecycleStatus.ACTIVE, reason=reason).to_dict()
        payload.update(
            {
                "tool": "approve",
                "id": memory_id,
                "status": LifecycleStatus.ACTIVE.value,
                "mutated": payload["mutation_count"] > 0,
                "policy": _lifecycle_policy_payload(config.agent_policy, LifecycleStatus.ACTIVE.value),
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="approve_failed", tool="approve", id=memory_id)


def reject_tool(memory_id: str, reason: Optional[str] = None, *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Reject a pending memory so default retrieval excludes it."""

    try:
        config = load_config(vault)
        payload = reject_memory(config, memory_id, reason=reason).to_dict()
        payload.update(
            {
                "tool": "reject",
                "id": memory_id,
                "status": LifecycleStatus.REJECTED.value,
                "mutated": payload["mutation_count"] > 0,
                "policy": _lifecycle_policy_payload(config.agent_policy, LifecycleStatus.REJECTED.value),
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="reject_failed", tool="reject", id=memory_id)


def mark_superseded_tool(
    old_id: str,
    by_id: str,
    reason: Optional[str] = None,
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Mark one memory superseded by another using the Stage 9 lifecycle service."""

    try:
        config = load_config(vault)
        payload = supersede_memory(config, old_id, new_id=by_id, reason=reason).to_dict()
        payload.update(
            {
                "tool": "mark_superseded",
                "old_id": old_id,
                "by_id": by_id,
                "mutated": payload["mutation_count"] > 0,
                "policy": _lifecycle_policy_payload(config.agent_policy, LifecycleStatus.SUPERSEDED.value),
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(
            exc,
            code="mark_superseded_failed",
            tool="mark_superseded",
            old_id=old_id,
            by_id=by_id,
        )


def create_server() -> Any:
    """Create a FastMCP server when the optional dependency is installed."""

    if FastMCP is None:
        raise RuntimeError(
            "Install the optional MCP dependency with `agent-memory[mcp]` to run the server."
        )

    server = FastMCP("Agent Memory")

    @server.tool()
    def remember(memory: dict[str, Any]) -> JsonPayload:
        """Create a pending, reviewable memory from an agent-supplied object."""

        return remember_tool(memory)

    @server.tool()
    def save_source(source: dict[str, Any]) -> JsonPayload:
        """Save raw material and an optional extract under Sources/."""

        return save_source_tool(source)

    @server.tool()
    def save_source_with_memories(
        source: dict[str, Any],
        memories: list[dict[str, Any]],
        author_name: Optional[str] = None,
    ) -> JsonPayload:
        """Save source/extract material and linked pending atomic memories."""

        return save_source_with_memories_tool(source, memories, author_name=author_name)

    @server.tool()
    def lookup_source(
        source_id: str,
        query: Optional[str] = None,
        budget: int = 800,
        session_id: Optional[str] = None,
        loaded_source_ids: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Return compact evidence from Sources/<source_id>/extract.md or source.md."""

        return lookup_source_tool(
            source_id,
            query=query,
            budget=budget,
            session_id=session_id,
            loaded_source_ids=loaded_source_ids,
        )

    @server.tool()
    def ingest_url(
        url: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        extract: Optional[str] = None,
        project: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Save URL material after the agent has fetched or summarized it."""

        return ingest_url_tool(url, title, content, extract, project, tags)

    @server.tool()
    def import_source(
        path: str,
        title: Optional[str] = None,
        extract_file: Optional[str] = None,
        project: Optional[str] = None,
        channel: str = "file",
        source_quality: str = "imported_export",
        sensitivity: str = "normal",
        tags: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Save a Markdown/text file as source material under Sources/."""

        return import_source_tool(
            path,
            title=title,
            extract_file=extract_file,
            project=project,
            channel=channel,
            source_quality=source_quality,
            sensitivity=sensitivity,
            tags=tags,
        )

    @server.tool()
    def import_source_inbox(
        path: str,
        project: Optional[str] = None,
        channel: str = "web_clipper",
        source_quality: str = "imported_export",
        sensitivity: str = "normal",
        tags: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> JsonPayload:
        """Import Markdown/text files from a source inbox directory."""

        return import_source_inbox_tool(
            path,
            project=project,
            channel=channel,
            source_quality=source_quality,
            sensitivity=sensitivity,
            tags=tags,
            dry_run=dry_run,
        )

    @server.tool()
    def import_session(
        path: str,
        summary: Optional[str] = None,
        summary_file: Optional[str] = None,
        remember_summary: bool = False,
        session_format: str = "text",
        project: Optional[str] = None,
        sensitivity: str = "normal",
        tags: Optional[list[str]] = None,
        confidence: float = 0.75,
    ) -> JsonPayload:
        """Save an AI-agent session transcript and optional pending summary memory."""

        return import_session_tool(
            path,
            summary=summary,
            summary_file=summary_file,
            remember_summary=remember_summary,
            session_format=session_format,
            project=project,
            sensitivity=sensitivity,
            tags=tags,
            confidence=confidence,
        )

    @server.tool()
    def search(query: str, filters: Optional[dict[str, Any]] = None) -> JsonPayload:
        """Search memory using keyword, metadata, and graph signals."""

        return search_tool(query, filters)

    @server.tool()
    def recall(
        query: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        loaded_memory_ids: Optional[list[str]] = None,
        loaded_source_ids: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Recall budgeted memory context from the indexed vault."""

        return recall_tool(
            query,
            budget,
            filters,
            session_id=session_id,
            loaded_memory_ids=loaded_memory_ids,
            loaded_source_ids=loaded_source_ids,
        )

    @server.tool()
    def brief(
        query: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        loaded_memory_ids: Optional[list[str]] = None,
        loaded_source_ids: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Generate a citation-preserving memory brief under a strict budget."""

        return brief_tool(
            query,
            budget,
            filters,
            session_id=session_id,
            loaded_memory_ids=loaded_memory_ids,
            loaded_source_ids=loaded_source_ids,
        )

    @server.tool()
    def build_profile(
        profile_type: str = "user",
        project: Optional[str] = None,
        budget: Optional[int] = None,
    ) -> JsonPayload:
        """Write generated user or project profile context under Profiles/."""

        return build_profile_tool(profile_type, project, budget)

    @server.tool()
    def should_recall(message: str) -> JsonPayload:
        """Classify whether a user request should be enriched with memory."""

        return should_recall_tool(message)

    @server.tool()
    def build_context(
        task: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        loaded_memory_ids: Optional[list[str]] = None,
        loaded_source_ids: Optional[list[str]] = None,
    ) -> JsonPayload:
        """Apply recall policy and return a memory brief only when useful."""

        return build_context_tool(
            task,
            budget,
            filters,
            session_id=session_id,
            loaded_memory_ids=loaded_memory_ids,
            loaded_source_ids=loaded_source_ids,
        )

    @server.tool()
    def inspect(id: str) -> JsonPayload:
        """Inspect a canonical memory by id."""

        return inspect_tool(id)

    @server.tool()
    def explain_recall(
        query: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
    ) -> JsonPayload:
        """Explain recall selection and skipped candidate reasons."""

        return explain_recall_tool(query, budget, filters)

    @server.tool()
    def mark_status(id: str, status: str) -> JsonPayload:
        """Set a memory lifecycle status."""

        return mark_status_tool(id, status)

    @server.tool()
    def review() -> JsonPayload:
        """List pending agent-generated memories awaiting review."""

        return review_tool()

    @server.tool()
    def curate(project: Optional[str] = None, source: Optional[str] = None) -> JsonPayload:
        """Propose conservative review actions without mutating memory files."""

        return curate_tool(project=project, source=source)

    @server.tool()
    def approve(id: str, reason: Optional[str] = None) -> JsonPayload:
        """Approve a pending memory by marking it active."""

        return approve_tool(id, reason)

    @server.tool()
    def reject(id: str, reason: Optional[str] = None) -> JsonPayload:
        """Reject a pending memory."""

        return reject_tool(id, reason)

    @server.tool()
    def mark_superseded(old_id: str, by_id: str, reason: Optional[str] = None) -> JsonPayload:
        """Mark one memory superseded by another."""

        return mark_superseded_tool(old_id, by_id, reason)

    return server


def main() -> None:
    """Run the MCP server over stdio for agent clients."""

    create_server().run()


def _placeholder_tool(command: str, *, vault: Optional[PathLike], **details: Any) -> JsonPayload:
    try:
        config = load_config(vault)
        payload = placeholder_result(command, vault_path=str(config.vault_path), **details)
        payload.update({"tool": command})
        return payload
    except Exception as exc:
        return _error_payload(exc, code=f"{command}_failed", tool=command)


def _optional_config(vault: Optional[PathLike]) -> Optional[MemoryConfig]:
    try:
        return load_config(vault)
    except ConfigError:
        return None


def _save_file_source(
    config: MemoryConfig,
    path: Path,
    *,
    title: Optional[str] = None,
    extract: Optional[str] = None,
    project: Optional[str] = None,
    tags: list[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
) -> dict[str, Any]:
    result = save_source_material(
        config,
        title=title or path.stem,
        content=path.read_text(encoding="utf-8"),
        extract=extract,
        project=project,
        tags=tags,
        channel=channel,
        source_quality=source_quality,
        sensitivity=sensitivity,
        origin={
            "provider": "file",
            "file_name": path.name,
            "path": str(path),
        },
    )
    return result.to_dict()


def _source_inbox_files(path: Path) -> list[Path]:
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in SOURCE_INBOX_SUFFIXES
    )


def _source_inbox_plan_payload(path: Path, candidates: list[Path], *, dry_run: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "implemented": True,
        "tool": "import_source_inbox",
        "command": "import-source-inbox",
        "dry_run": dry_run,
        "inbox_path": str(path),
        "source_count": len(candidates),
        "sources": [
            {
                "path": str(candidate),
                "title": candidate.stem,
                "suffix": candidate.suffix.lower(),
            }
            for candidate in candidates
        ],
    }


def _expanded_path(path: PathLike) -> Path:
    return Path(path).expanduser()


def _read_optional_text_file(path: Optional[PathLike]) -> Optional[str]:
    if path is None:
        return None
    return _expanded_path(path).read_text(encoding="utf-8")


def _agent_memory_status(
    memory: Mapping[str, Any],
    policy: AgentPolicyConfig,
    confidence: float,
) -> LifecycleStatus:
    requested = _optional_string(memory.get("status"))
    requested_status = LifecycleStatus(requested) if requested else None
    explicit_user_save = _explicit_user_save(memory)
    confirmed = _bool(memory.get("confirmed_by_user") or memory.get("user_confirmed"))
    trust_level = _trust_level(policy)

    if trust_level == AgentTrustLevel.MANUAL.value and not confirmed:
        raise ValueError("agent_policy.trust_level=manual requires confirmed_by_user before saving memory")

    if requested_status and requested_status != LifecycleStatus.ACTIVE:
        return requested_status if _agent_can_choose_status(policy, requested_status) else LifecycleStatus.PENDING

    active_allowed = (
        confidence >= policy.min_active_confidence
        and (
            trust_level == AgentTrustLevel.AUTONOMOUS.value
            or (
                trust_level == AgentTrustLevel.EXPLICIT_ACTIVE.value
                and policy.explicit_user_saves_active
                and explicit_user_save
            )
        )
    )
    if (requested_status == LifecycleStatus.ACTIVE or explicit_user_save) and active_allowed:
        return LifecycleStatus.ACTIVE
    return LifecycleStatus.PENDING


def _agent_can_choose_status(policy: AgentPolicyConfig, status: LifecycleStatus) -> bool:
    trust_level = _trust_level(policy)
    if trust_level == AgentTrustLevel.AUTONOMOUS.value:
        return True
    return status == LifecycleStatus.PENDING


def _explicit_user_save(memory: Mapping[str, Any]) -> bool:
    return any(
        _bool(memory.get(key))
        for key in (
            "explicit_user_save",
            "user_explicit",
            "authorized_by_user",
            "direct_user_instruction",
        )
    )


def _agent_policy_payload(
    policy: AgentPolicyConfig,
    *,
    selected_status: LifecycleStatus,
    confidence: float,
    explicit_user_save: bool,
) -> dict[str, Any]:
    return {
        "trust_level": _trust_level(policy),
        "selected_status": selected_status.value,
        "explicit_user_save": explicit_user_save,
        "review_required": selected_status == LifecycleStatus.PENDING,
        "confidence": confidence,
        "min_active_confidence": policy.min_active_confidence,
        "min_pending_confidence": policy.min_pending_confidence,
    }


def _lifecycle_policy_payload(policy: AgentPolicyConfig, status: str) -> dict[str, Any]:
    trust_level = _trust_level(policy)
    terminal_status = status in {
        LifecycleStatus.REJECTED.value,
        LifecycleStatus.STALE.value,
        LifecycleStatus.SUPERSEDED.value,
    }
    return {
        "trust_level": trust_level,
        "autonomous_lifecycle": policy.autonomous_lifecycle,
        "requires_user_confirmation": terminal_status
        and not (trust_level == AgentTrustLevel.AUTONOMOUS.value and policy.autonomous_lifecycle),
    }


def _trust_level(policy: AgentPolicyConfig) -> str:
    value = policy.trust_level
    return value.value if isinstance(value, AgentTrustLevel) else str(value)


def _memory_text(memory: Mapping[str, Any]) -> str:
    for key in ("text", "body", "content"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError("memory must include non-empty text")


def _memory_source(memory: Mapping[str, Any]) -> SourceRef:
    source = memory.get("source")
    if isinstance(source, Mapping):
        return SourceRef.model_validate(dict(source))
    if isinstance(source, str) and source.strip():
        return SourceRef(path=source.strip())
    return SourceRef(path="MCP/agent-provided-memory.md", title="Agent-provided MCP memory")


def _vault_from(memory: Mapping[str, Any], vault: Optional[PathLike]) -> Optional[PathLike]:
    return vault or _optional_string(memory.get("vault"))


def _filters(filters: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    return dict(filters or {})


def _session_inputs(
    filters: dict[str, Any],
    *,
    session_id: Any = None,
    loaded_memory_ids: Any = None,
    loaded_source_ids: Any = None,
) -> dict[str, Any]:
    filter_session_id = filters.pop("session_id", None)
    filter_loaded_memory_ids = _combine_session_id_inputs(
        filters.pop("loaded_memory_ids", None),
        filters.pop("loaded_memory_id", None),
    )
    filter_loaded_source_ids = _combine_session_id_inputs(
        filters.pop("loaded_source_ids", None),
        filters.pop("loaded_source_id", None),
    )
    return {
        "session_id": session_id if session_id is not None else filter_session_id,
        "loaded_memory_ids": normalize_loaded_ids(
            loaded_memory_ids if loaded_memory_ids is not None else filter_loaded_memory_ids
        ),
        "loaded_source_ids": normalize_loaded_ids(
            loaded_source_ids if loaded_source_ids is not None else filter_loaded_source_ids
        ),
    }


def _combine_session_id_inputs(*values: Any) -> list[Any]:
    combined: list[Any] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            combined.append(value)
            continue
        try:
            combined.extend(value)
        except TypeError:
            combined.append(value)
    return combined


def _budget(value: int) -> int:
    budget = int(value)
    if budget < 1:
        raise ValueError("budget must be at least 1")
    return budget


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _build_context_trace(
    policy: Mapping[str, Any],
    *,
    task_class: str = "default",
    task_policy: Optional[TaskRecallPolicyConfig] = None,
    profile: Optional[Mapping[str, Any]] = None,
    task_budget: Optional[int] = None,
    retrieval: Optional[Any] = None,
    freshness: Optional[Any] = None,
    session: Optional[Any] = None,
    selected_count: int = 0,
    empty_reason: Optional[str] = None,
) -> dict[str, Any]:
    retrieval_trace = dict(retrieval) if isinstance(retrieval, Mapping) else {}
    policy_query = str(policy.get("query") or "")
    planned = retrieval_trace.get("planned_query_variants")
    if not isinstance(planned, list):
        planned = [policy_query] if policy_query else []
    semantic = retrieval_trace.get("semantic")
    if not isinstance(semantic, Mapping):
        semantic = {
            "status": "not_used",
            "enabled": False,
            "provider": None,
            "model": None,
        }
    attempts = retrieval_trace.get("attempted_searches")
    if not isinstance(attempts, list):
        attempts = []
    freshness_trace = dict(freshness) if isinstance(freshness, Mapping) else None
    session_trace_payload = dict(session) if isinstance(session, Mapping) else None
    profile_trace = dict(profile) if isinstance(profile, Mapping) else _build_context_profile_trace(
        None,
        task_policy,
        requested=False,
        request_sources=[],
        project=None,
        task_budget=task_budget,
    )
    selected_task_budget = int(task_budget or (task_policy.budget if task_policy is not None else 0))
    payload: dict[str, Any] = {
        "policy": dict(policy),
        "task_class": task_class,
        "recall_policy": task_policy.model_dump(mode="json") if task_policy is not None else None,
        "task_budget": {
            "selected": selected_task_budget,
            "brief": selected_task_budget,
            "profile": int(profile_trace.get("budget") or 0),
            "profile_used": int(profile_trace.get("used_tokens_estimate") or 0),
        },
        "profile": profile_trace,
        "policy_query": policy_query,
        "planned_query_variants": planned,
        "mode": retrieval_trace.get("mode"),
        "requested_mode": retrieval_trace.get("requested_mode"),
        "semantic": dict(semantic),
        "attempted_searches": attempts,
        "freshness": freshness_trace,
        "selected_count": selected_count,
        "empty_reason": empty_reason or retrieval_trace.get("empty_reason"),
        "recall_ladder": [
            {
                "step": "policy",
                "memory_needed": bool(policy.get("should_recall")),
            },
            {
                "step": "profile",
                "included": bool(profile_trace.get("included")),
                "reason": profile_trace.get("reason"),
            },
            {
                "step": "brief",
                "selected_count": selected_count,
                "empty_reason": empty_reason or retrieval_trace.get("empty_reason"),
            },
        ],
    }
    if session_trace_payload:
        payload["session"] = session_trace_payload
    return payload


def _refresh_index_for_query(config: MemoryConfig, *, before: str) -> Optional[dict[str, Any]]:
    """Refresh the derived index for MCP retrieval paths when configured."""

    freshness_config = config.index_freshness
    if before == "search":
        enabled = freshness_config.refresh_before_search
    elif before == "recall":
        enabled = freshness_config.refresh_before_recall
    else:
        raise ValueError("before must be 'search' or 'recall'")
    if not enabled:
        return {
            "enabled": freshness_config.enabled,
            "trigger": f"before_{before}",
            "skipped": True,
            "reason": "disabled_for_operation",
        }
    payload = refresh_index_if_needed(config, debounce_seconds=0).to_dict()
    payload.update({"trigger": f"before_{before}", "skipped": False})
    return payload


def _resolve_build_context_profile_request(
    config: Optional[MemoryConfig],
    task_policy: TaskRecallPolicyConfig,
    override: Any,
) -> tuple[bool, list[str]]:
    if override is not None:
        return _bool(override), ["filter"]
    sources: list[str] = []
    requested = False
    if config is not None and config.profile.inject_by_default:
        requested = True
        sources.append("config")
    if task_policy.include_profile:
        requested = True
        sources.append("task_policy")
    return requested, sources


def _build_context_profile_trace(
    config: Optional[MemoryConfig],
    task_policy: Optional[TaskRecallPolicyConfig],
    *,
    requested: bool,
    request_sources: list[str],
    project: Optional[str],
    task_budget: Optional[int],
) -> dict[str, Any]:
    if config is None:
        profile_type = "project" if project else "user"
        return {
            "included": False,
            "requested": requested,
            "request_sources": list(request_sources),
            "reason": "config_unavailable" if requested else "profile_injection_disabled",
            "profile_type": profile_type,
            "project": project,
            "budget": 0,
            "used_tokens_estimate": 0,
            "memory_count": 0,
            "citations": [],
        }
    return build_context_profile_payload(
        config,
        requested=requested,
        request_sources=request_sources,
        project=project,
        task_budget=task_budget,
    )


def _policy_skipped_profile_payload(
    config: Optional[MemoryConfig],
    task_policy: TaskRecallPolicyConfig,
    *,
    requested: bool,
    request_sources: list[str],
    project: Optional[str],
    task_budget: int,
) -> dict[str, Any]:
    payload = _build_context_profile_trace(
        config,
        task_policy,
        requested=False,
        request_sources=request_sources,
        project=project,
        task_budget=task_budget,
    )
    payload["requested"] = requested
    payload["reason"] = "policy_skipped" if requested else "profile_injection_disabled"
    return payload


def _compose_context_markdown(profile: Mapping[str, Any], brief_markdown: str) -> str:
    profile_markdown = str(profile.get("markdown") or "") if profile.get("included") else ""
    if not profile_markdown:
        return brief_markdown
    if not brief_markdown:
        return profile_markdown
    return f"{profile_markdown.rstrip()}\n\n{brief_markdown}"


def _resolve_task_recall_policy(
    config: Optional[MemoryConfig],
    task_class: Any,
) -> tuple[str, TaskRecallPolicyConfig]:
    selected = _optional_string(task_class)
    key = (selected or "default").strip().lower()
    policies = config.recall_policies if config is not None else {}
    policy = policies.get(key) or policies.get("default") or TaskRecallPolicyConfig()
    return key if key in policies else "default", policy


def _build_context_budget(
    budget: int,
    task_policy: TaskRecallPolicyConfig,
    agent_policy: AgentPolicyConfig,
) -> int:
    if int(budget or 0) == agent_policy.default_recall_budget:
        return _budget(task_policy.budget)
    return _budget(budget or agent_policy.default_recall_budget)


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _optional_enum(enum_type: Any, value: Any) -> Any:
    if value in (None, ""):
        return None
    return enum_type(value)


def _citation(memory_id: str, path: str) -> dict[str, str]:
    return {"id": memory_id, "path": path, "kind": "memory"}


def _error_payload(exc: Exception, *, code: str, **details: Any) -> JsonPayload:
    if isinstance(exc, ConfigError):
        code = "config_error"
    return {
        "ok": False,
        **details,
        "error": {
            "code": code,
            "message": str(exc),
        },
        "citations": [],
    }


__all__ = [
    "approve_tool",
    "brief_tool",
    "build_profile_tool",
    "build_context_tool",
    "create_server",
    "curate_tool",
    "explain_recall_tool",
    "import_session_tool",
    "import_source_inbox_tool",
    "import_source_tool",
    "inspect_tool",
    "lookup_source_tool",
    "main",
    "mark_superseded_tool",
    "mark_status_tool",
    "recall_tool",
    "remember_tool",
    "reject_tool",
    "review_tool",
    "ingest_url_tool",
    "save_source_tool",
    "save_source_with_memories_tool",
    "search_tool",
    "should_recall_tool",
]


if __name__ == "__main__":  # pragma: no cover
    main()

