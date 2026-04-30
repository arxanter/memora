"""MCP tool handlers and optional FastMCP server registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Union

from agent_memory.brief import brief_memory
from agent_memory.config import ConfigError, load_config
from agent_memory.lifecycle import mark_status
from agent_memory.recall import recall_memory
from agent_memory.retrieval import RetrievalIndexError, SearchFilters, search_memory
from agent_memory.schema import (
    AuthorKind,
    LifecycleStatus,
    MemoryScope,
    MemoryType,
    SourceRef,
    iter_memory_markdown_files,
    validate_markdown_file,
)
from agent_memory.vault import placeholder_result, remember_memory

try:  # pragma: no cover - exercised only when the optional MCP extra is installed.
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]

JsonPayload = dict[str, Any]
PathLike = Union[Path, str]


def remember_tool(memory: Mapping[str, Any], *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Create a pending agent-authored memory using the shared vault service."""

    try:
        config = load_config(_vault_from(memory, vault))
        memory_type = MemoryType(memory.get("type", MemoryType.FACT.value))
        text = _memory_text(memory)
        confidence = float(memory.get("confidence", 0.5))
        source = _memory_source(memory)
        author_name = _optional_string(memory.get("author_name")) or "MCP agent"
        result = remember_memory(
            config,
            memory_type=memory_type,
            text=text,
            scope=_optional_enum(MemoryScope, memory.get("scope")),
            project=_optional_string(memory.get("project")),
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
                "citations": [_citation(payload["id"], payload["relative_path"])],
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="remember_failed")


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
    limit = int(raw_filters.pop("limit", 10))
    try:
        config = load_config(vault)
        payload = search_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            include_related=include_related,
            limit=limit,
            semantic=None if semantic is None else _bool(semantic),
        ).to_dict()
        payload.update({"tool": "search"})
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
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Recall ranked memory chunks packed under a strict token budget."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="recall")

    raw_filters = _filters(filters)
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    try:
        config = load_config(vault)
        payload = recall_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            budget=selected_budget,
            include_related=include_related,
            semantic=None if semantic is None else _bool(semantic),
        ).to_dict()
        payload.update({"tool": "recall"})
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
    *,
    vault: Optional[PathLike] = None,
) -> JsonPayload:
    """Generate a citation-preserving memory brief under a strict budget."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="brief")

    raw_filters = _filters(filters)
    include_related = _bool(raw_filters.pop("include_related", False))
    semantic = raw_filters.pop("semantic", None)
    try:
        config = load_config(vault)
        payload = brief_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(raw_filters),
            budget=selected_budget,
            include_related=include_related,
            semantic=None if semantic is None else _bool(semantic),
        ).to_dict()
        payload.update({"tool": "brief"})
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


def inspect_tool(memory_id: str, *, vault: Optional[PathLike] = None) -> JsonPayload:
    """Inspect one Markdown memory by id using the Stage 1 validator."""

    try:
        config = load_config(vault)
        for path in iter_memory_markdown_files(config.vault_path):
            document = validate_markdown_file(path)
            if document.frontmatter.id != memory_id:
                continue

            relative_path = path.relative_to(config.vault_path).as_posix()
            return {
                "ok": True,
                "tool": "inspect",
                "id": memory_id,
                "found": True,
                "memory": document.frontmatter.model_dump(mode="json"),
                "body": document.body.strip(),
                "citations": [_citation(memory_id, relative_path)],
            }

        return {
            "ok": False,
            "tool": "inspect",
            "id": memory_id,
            "found": False,
            "error": {
                "code": "memory_not_found",
                "message": f"memory not found: {memory_id}",
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
    """Stage 3 stable placeholder for future recall explanations."""

    try:
        selected_budget = _budget(budget)
    except Exception as exc:
        return _error_payload(exc, code="invalid_budget", tool="explain_recall")

    return _placeholder_tool(
        "explain_recall",
        vault=vault,
        query=query,
        budget=selected_budget,
        filters=_filters(filters),
        explanation=[],
        citations=[],
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
            }
        )
        return payload
    except Exception as exc:
        return _error_payload(exc, code="mark_status_failed", tool="mark_status", id=memory_id)


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
    def search(query: str, filters: Optional[dict[str, Any]] = None) -> JsonPayload:
        """Search memory using keyword, metadata, and graph signals."""

        return search_tool(query, filters)

    @server.tool()
    def recall(
        query: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
    ) -> JsonPayload:
        """Recall budgeted memory context from the indexed vault."""

        return recall_tool(query, budget, filters)

    @server.tool()
    def brief(
        query: str,
        budget: int = 1200,
        filters: Optional[dict[str, Any]] = None,
    ) -> JsonPayload:
        """Generate a citation-preserving memory brief under a strict budget."""

        return brief_tool(query, budget, filters)

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
        """Explain recall selection. Stage 3 returns a stable placeholder."""

        return explain_recall_tool(query, budget, filters)

    @server.tool()
    def mark_status(id: str, status: str) -> JsonPayload:
        """Set a memory lifecycle status."""

        return mark_status_tool(id, status)

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
    "brief_tool",
    "create_server",
    "explain_recall_tool",
    "inspect_tool",
    "main",
    "mark_status_tool",
    "recall_tool",
    "remember_tool",
    "search_tool",
]


if __name__ == "__main__":  # pragma: no cover
    main()

