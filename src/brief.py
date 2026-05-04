"""Deterministic citation-preserving memora brief generation."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from config import MemoryConfig
from indexer import estimate_tokens
from recall import PackedChunk, RecallResponse, recall_memory
from retrieval import SearchFilters
from schema import LifecycleStatus, MemoryType, RelationType


SECTION_TITLES = {
    "current_relevant_facts": "Current relevant facts",
    "current_decisions": "Current decisions",
    "warnings": "Warnings",
    "open_questions": "Open questions",
}
SECTION_ORDER = tuple(SECTION_TITLES)
FACT_TYPES = {
    MemoryType.FACT.value,
    MemoryType.PREFERENCE.value,
    MemoryType.PROJECT_CONTEXT.value,
    MemoryType.CONVERSATION_SUMMARY.value,
    MemoryType.SOURCE_EXTRACT.value,
}
WARNING_STATUSES = {
    LifecycleStatus.PENDING.value,
    LifecycleStatus.REJECTED.value,
    LifecycleStatus.STALE.value,
    LifecycleStatus.SUPERSEDED.value,
}


@dataclass(frozen=True)
class BriefItem:
    """One deterministic bullet in a memora brief section."""

    section: str
    text: str
    citations: tuple[dict[str, Any], ...]
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    source_status: Optional[str] = None

    def to_dict(self, citation_keys: Mapping[str, str]) -> dict[str, Any]:
        return {
            "text": self.text,
            "citations": [_citation_key(citation_keys, citation) for citation in self.citations],
            "source_id": self.source_id,
            "type": self.source_type,
            "status": self.source_status,
        }


@dataclass(frozen=True)
class BriefResponse:
    """Structured brief response returned by CLI surfaces."""

    config: MemoryConfig
    query: str
    filters: SearchFilters
    budget: int
    markdown: str
    sections: Mapping[str, tuple[BriefItem, ...]]
    citations: tuple[dict[str, Any], ...]
    citation_keys: Mapping[str, str]
    recall: RecallResponse
    truncated: bool = False

    @property
    def used_tokens_estimate(self) -> int:
        if not self.markdown.strip():
            return 0
        return estimate_tokens(self.markdown)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": True,
            "implemented": True,
            "query": self.query,
            "filters": self.filters.to_dict(),
            "budget": self.budget,
            "budget_mode": "strict",
            "used_tokens_estimate": self.used_tokens_estimate,
            "vault_path": str(self.config.vault_path),
            "index_path": str(self.config.index_file),
            "markdown": self.markdown,
            "sections": {
                section: [
                    item.to_dict(self.citation_keys) for item in self.sections.get(section, ())
                ]
                for section in SECTION_ORDER
            },
            "citations": [dict(citation) for citation in self.citations],
            "recall": {
                "candidate_count": self.recall.candidate_count,
                "chunk_count": len(self.recall.chunks),
                "used_tokens_estimate": self.recall.used_tokens_estimate,
                "retrieval": dict(self.recall.retrieval_trace),
            },
            "retrieval": dict(self.recall.retrieval_trace),
            "truncated": self.truncated,
        }
        if self.recall.session:
            payload["session"] = dict(self.recall.session)
            payload["recall"]["session"] = dict(self.recall.session)
        return payload


def brief_memory(
    config: MemoryConfig,
    query: str,
    *,
    filters: Optional[SearchFilters] = None,
    budget: int = 1200,
    include_related: bool = False,
    semantic: Optional[bool] = None,
    mode: str = "auto",
    recall_response: Optional[RecallResponse] = None,
    session_id: Any = None,
    loaded_memory_ids: Any = None,
    loaded_source_ids: Any = None,
) -> BriefResponse:
    """Build a deterministic memora brief from Stage 7 recall output."""

    selected_budget = _validate_budget(budget)
    recall = recall_response or recall_memory(
        config,
        query,
        filters=filters,
        budget=selected_budget,
        include_related=include_related,
        semantic=semantic,
        mode=mode,
        session_id=session_id,
        loaded_memory_ids=loaded_memory_ids,
        loaded_source_ids=loaded_source_ids,
    )
    selected_filters = SearchFilters.from_mapping(recall.filters.to_dict())
    items = _items_from_recall(config, recall)
    sections, markdown, citations, citation_keys, truncated = _fit_markdown(items, selected_budget)
    return BriefResponse(
        config=config,
        query=recall.query,
        filters=selected_filters,
        budget=selected_budget,
        markdown=markdown,
        sections=sections,
        citations=citations,
        citation_keys=citation_keys,
        recall=recall,
        truncated=truncated,
    )


def render_brief_markdown(
    sections: Mapping[str, Sequence[BriefItem]],
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, str]]:
    """Render sections into the stable Markdown brief shape."""

    citations, citation_keys = _collect_citations(sections)
    lines = ["## Memora Brief", ""]
    for section in SECTION_ORDER:
        lines.append(f"{SECTION_TITLES[section]}:")
        for item in sections.get(section, ()):
            keys = [_citation_key(citation_keys, citation) for citation in item.citations]
            suffix = f" [{', '.join(keys)}]" if keys else ""
            lines.append(f"- {item.text}{suffix}")
        lines.append("")
    lines.append("Citations:")
    for citation in citations:
        lines.append(f"- [{citation['key']}] {citation['path']}")
    return "\n".join(lines).rstrip() + "\n", citations, citation_keys


def _items_from_recall(config: MemoryConfig, recall: RecallResponse) -> tuple[BriefItem, ...]:
    items = [_item_from_chunk(chunk) for chunk in recall.chunks]
    items = [item for item in items if item is not None]
    items.extend(_graph_items(config, recall.chunks))
    return tuple(items)


def _item_from_chunk(chunk: PackedChunk) -> Optional[BriefItem]:
    metadata = dict(chunk.metadata)
    memory_type = str(metadata.get("type") or "")
    status = str(metadata.get("status") or "")
    text = _brief_text(chunk.text)
    if not text:
        return None

    citation = _chunk_citation(chunk)
    if status in WARNING_STATUSES:
        label = (
            "Stale" if status == LifecycleStatus.STALE.value else status.replace("_", " ").title()
        )
        return BriefItem(
            section="warnings",
            text=f"{label}: {text}",
            citations=(citation,),
            source_id=chunk.document_id,
            source_type=memory_type,
            source_status=status,
        )
    if memory_type == MemoryType.DECISION.value:
        section = "current_decisions"
    elif memory_type == MemoryType.TASK.value:
        section = "open_questions"
    elif memory_type in FACT_TYPES:
        section = "current_relevant_facts"
    else:
        section = "current_relevant_facts"

    return BriefItem(
        section=section,
        text=text,
        citations=(citation,),
        source_id=chunk.document_id,
        source_type=memory_type,
        source_status=status,
    )


def _graph_items(config: MemoryConfig, chunks: Sequence[PackedChunk]) -> list[BriefItem]:
    document_ids = {chunk.document_id for chunk in chunks}
    if not document_ids:
        return []

    placeholders = ", ".join("?" for _ in document_ids)
    with sqlite3.connect(config.index_file) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT
                l.from_id,
                l.to_id,
                l.relation,
                from_doc.path AS from_path,
                to_doc.path AS to_path
            FROM links l
            LEFT JOIN documents from_doc ON from_doc.id = l.from_id
            LEFT JOIN documents to_doc ON to_doc.id = l.to_id
            WHERE (l.from_id IN ({placeholders}) OR l.to_id IN ({placeholders}))
              AND l.relation IN (?, ?)
            ORDER BY l.relation ASC, l.from_id ASC, l.to_id ASC
            """,
            (
                *document_ids,
                *document_ids,
                RelationType.CONTRADICTS.value,
                RelationType.SUPERSEDES.value,
            ),
        ).fetchall()

    selected_citations = {chunk.document_id: _chunk_citation(chunk) for chunk in chunks}
    items: list[BriefItem] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        from_id = str(row["from_id"])
        to_id = str(row["to_id"])
        relation = str(row["relation"])
        signature = (relation, from_id, to_id)
        if signature in seen:
            continue
        seen.add(signature)
        citations = tuple(
            citation
            for citation in (
                selected_citations.get(from_id)
                or _document_citation(from_id, row["from_path"], relation),
                selected_citations.get(to_id)
                or _document_citation(to_id, row["to_path"], relation),
            )
            if citation is not None
        )
        if relation == RelationType.CONTRADICTS.value:
            items.append(
                BriefItem(
                    section="open_questions",
                    text=f"Conflict detected: {from_id} contradicts {to_id}.",
                    citations=citations,
                    source_id=from_id,
                    source_type="relation",
                    source_status=None,
                )
            )
        elif relation == RelationType.SUPERSEDES.value:
            items.append(
                BriefItem(
                    section="warnings",
                    text=f"Superseded memory: {to_id} is superseded by {from_id}.",
                    citations=citations,
                    source_id=from_id,
                    source_type="relation",
                    source_status=None,
                )
            )
    return items


def _fit_markdown(
    items: Sequence[BriefItem],
    budget: int,
) -> tuple[dict[str, tuple[BriefItem, ...]], str, tuple[dict[str, Any], ...], dict[str, str], bool]:
    sections: dict[str, list[BriefItem]] = {section: [] for section in SECTION_ORDER}
    base_markdown, _, _ = render_brief_markdown(_freeze_sections(sections))
    if estimate_tokens(base_markdown) > budget:
        minimal = "## Memora Brief\n"
        if estimate_tokens(minimal) <= budget:
            return _freeze_sections(sections), minimal, (), {}, True
        return _freeze_sections(sections), "", (), {}, True

    truncated = False
    for item in sorted(items, key=_budget_priority):
        candidate_sections = {section: list(values) for section, values in sections.items()}
        candidate_sections[item.section].append(item)
        markdown, _, _ = render_brief_markdown(_freeze_sections(candidate_sections))
        if estimate_tokens(markdown) <= budget:
            sections = candidate_sections
        else:
            truncated = True

    frozen = _freeze_sections(sections)
    markdown, citations, citation_keys = render_brief_markdown(frozen)
    return frozen, markdown, citations, citation_keys, truncated


def _budget_priority(item: BriefItem) -> tuple[int, str, str]:
    section_priority = {
        "warnings": 0,
        "current_decisions": 1,
        "current_relevant_facts": 2,
        "open_questions": 3,
    }
    return (section_priority.get(item.section, 9), item.source_id or "", item.text)


def _freeze_sections(
    sections: Mapping[str, Sequence[BriefItem]],
) -> dict[str, tuple[BriefItem, ...]]:
    return {section: tuple(sections.get(section, ())) for section in SECTION_ORDER}


def _collect_citations(
    sections: Mapping[str, Sequence[BriefItem]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    citations: list[dict[str, Any]] = []
    citation_keys: dict[str, str] = {}
    for section in SECTION_ORDER:
        for item in sections.get(section, ()):
            for citation in item.citations:
                signature = _citation_signature(citation)
                if signature in citation_keys:
                    continue
                key = f"C{len(citations) + 1}"
                citation_keys[signature] = key
                citations.append({"key": key, **citation})
    return tuple(citations), citation_keys


def _citation_key(citation_keys: Mapping[str, str], citation: Mapping[str, Any]) -> str:
    return citation_keys[_citation_signature(citation)]


def _citation_signature(citation: Mapping[str, Any]) -> str:
    return "|".join(
        str(citation.get(key) or "") for key in ("id", "path", "chunk_id", "chunk_type", "relation")
    )


def _chunk_citation(chunk: PackedChunk) -> dict[str, Any]:
    return {
        "id": chunk.document_id,
        "path": chunk.path,
        "kind": "memory",
        "chunk_id": chunk.chunk_id,
        "chunk_type": chunk.chunk_type,
        "truncated": chunk.truncated,
    }


def _document_citation(memory_id: str, path: Any, relation: str) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    return {
        "id": memory_id,
        "path": str(path),
        "kind": "memory",
        "relation": relation,
    }


def _brief_text(text: str, *, max_words: int = 32) -> str:
    cleaned_lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned_lines.append(re.sub(r"^[-*]\s+", "", stripped))
    cleaned = re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    return " ".join(words[:max_words]).rstrip(".,;:") + "..."


def _validate_budget(value: int) -> int:
    budget = int(value)
    if budget < 1:
        raise ValueError("budget must be at least 1")
    return budget


__all__ = [
    "BriefItem",
    "BriefResponse",
    "brief_memory",
    "render_brief_markdown",
]
