"""Deterministic synthesis output for active canonical memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from config import MemoryConfig
from markdown import aliases as presentation_aliases
from markdown import wikilink_for_memory
from schema import LifecycleStatus, MemoryDocument, MemoryType, validate_vault
from sync import atomic_write_text, vault_lock

SYNTHESIS_SCHEMA = "memora.synthesis.v1"
DEFAULT_LIMIT = 20
TYPE_ORDER = (
    MemoryType.DECISION.value,
    MemoryType.FACT.value,
    MemoryType.PREFERENCE.value,
    MemoryType.TASK.value,
    MemoryType.PROJECT_CONTEXT.value,
    MemoryType.SOURCE_EXTRACT.value,
    MemoryType.CONVERSATION_SUMMARY.value,
)
TYPE_TITLES = {
    MemoryType.DECISION.value: "Decisions",
    MemoryType.FACT.value: "Facts",
    MemoryType.PREFERENCE.value: "Preferences",
    MemoryType.TASK.value: "Tasks",
    MemoryType.PROJECT_CONTEXT.value: "Project Context",
    MemoryType.SOURCE_EXTRACT.value: "Source Extracts",
    MemoryType.CONVERSATION_SUMMARY.value: "Conversation Summaries",
}
NEXT_STEPS = (
    "Review this generated synthesis manually before treating it as durable knowledge.",
    "Promote durable conclusions with `memora remember`; synthesis does not create canonical memories.",
)


@dataclass(frozen=True)
class SynthesisItem:
    """One selected memory rendered as a synthesis bullet."""

    memory_id: str
    memory_type: str
    summary: str
    citation_key: str
    relative_path: Path
    source: Optional[dict[str, Any]] = None

    def citation(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.citation_key,
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "type": self.memory_type,
        }
        if self.source:
            payload["source"] = dict(self.source)
        return payload


@dataclass(frozen=True)
class SynthesisResult:
    """Structured result for CLI and agent-friendly surfaces."""

    config: MemoryConfig
    path: Path
    relative_path: Path
    title: str
    project: Optional[str]
    generated_at: datetime
    markdown: str
    items: tuple[SynthesisItem, ...]
    query: Optional[str] = None
    dry_run: bool = False
    written: bool = True

    @property
    def citations(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.citation() for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "command": "synthesize",
            "schema": SYNTHESIS_SCHEMA,
            "title": self.title,
            "project": self.project,
            "query": self.query,
            "filters": _result_filters(project=self.project, query=self.query),
            "generated_at": self.generated_at.isoformat(),
            "vault_path": str(self.config.vault_path),
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "dry_run": self.dry_run,
            "would_write": self.dry_run,
            "written": self.written,
            "memory_count": len(self.items),
            "source_memory_ids": [item.memory_id for item in self.items],
            "citations": [dict(citation) for citation in self.citations],
            "markdown": self.markdown,
            "next_steps": list(NEXT_STEPS),
        }


def plan_synthesis(
    config: MemoryConfig,
    *,
    project: Optional[str] = None,
    query: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    now: Optional[datetime] = None,
) -> SynthesisResult:
    """Plan deterministic Markdown synthesis without writing a note."""

    return _synthesize(config, project=project, query=query, title=title, limit=limit, now=now, write=False)


def write_synthesis(
    config: MemoryConfig,
    *,
    project: Optional[str] = None,
    query: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    now: Optional[datetime] = None,
) -> SynthesisResult:
    """Write a deterministic Markdown synthesis under the vault's Synthesis directory."""

    return _synthesize(config, project=project, query=query, title=title, limit=limit, now=now, write=True)


def _synthesize(
    config: MemoryConfig,
    *,
    project: Optional[str],
    query: Optional[str],
    title: Optional[str],
    limit: int,
    now: Optional[datetime],
    write: bool,
) -> SynthesisResult:
    selected_limit = _validate_limit(limit)
    selected_project = _clean_optional(project)
    selected_query = _clean_optional(query)
    selected_title = _synthesis_title(title=title, project=selected_project, query=selected_query)
    generated_at = _normalize_now(now)

    report = validate_vault(config.vault_path)
    if report.issues:
        first_issue = report.issues[0]
        raise ValueError(f"cannot synthesize invalid vault: {first_issue.path}: {first_issue.message}")

    documents = _select_documents(
        report.documents,
        config=config,
        project=selected_project,
        query=selected_query,
        limit=selected_limit,
    )
    items = tuple(
        _item_from_document(config, document, citation_key=f"C{position}")
        for position, document in enumerate(documents, start=1)
    )
    markdown = render_synthesis_markdown(
        title=selected_title,
        project=selected_project,
        query=selected_query,
        generated_at=generated_at,
        items=items,
    )

    synthesis_root = config.vault_path / config.synthesis_dir
    if write:
        with vault_lock(config, name="synthesis"):
            synthesis_root.mkdir(parents=True, exist_ok=True)
            target_path = _unique_synthesis_path(synthesis_root, generated_at=generated_at, title=selected_title)
            atomic_write_text(target_path, markdown)
    else:
        target_path = _unique_synthesis_path(synthesis_root, generated_at=generated_at, title=selected_title)

    return SynthesisResult(
        config=config,
        path=target_path,
        relative_path=target_path.relative_to(config.vault_path),
        title=selected_title,
        project=selected_project,
        query=selected_query,
        generated_at=generated_at,
        markdown=markdown,
        items=items,
        dry_run=not write,
        written=write,
    )


def render_synthesis_markdown(
    *,
    title: str,
    project: Optional[str],
    query: Optional[str] = None,
    generated_at: datetime,
    items: Sequence[SynthesisItem],
) -> str:
    """Render synthesis Markdown with non-canonical generated frontmatter."""

    frontmatter = {
        "schema": SYNTHESIS_SCHEMA,
        "kind": "generated_synthesis",
        "title": title,
        "aliases": presentation_aliases(title, _synthesis_alias(project=project)),
        "project": project,
        "query": query,
        "created_at": generated_at.isoformat(),
        "generated_at": generated_at.isoformat(),
        "source_memory_ids": [item.memory_id for item in items],
        "source_memory_count": len(items),
        "source_citations": [item.citation() for item in items],
        "generated_by": "memora synthesize",
    }
    rendered_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    lines = [
        "---",
        rendered_yaml,
        "---",
        "",
        f"# {title}",
        "",
        f"Generated at: {generated_at.isoformat()}",
    ]
    if project:
        lines.append(f"Project: {project}")
    if query:
        lines.append(f"Query: {query}")
    lines.extend(
        [
            f"Selected active memories: {len(items)}",
            "",
        ]
    )

    if not items:
        lines.extend(
            [
                "No active memories matched the synthesis filters.",
                "",
            ]
        )
    else:
        for memory_type in _ordered_types(items):
            lines.append(f"## {TYPE_TITLES.get(memory_type, _title_from_type(memory_type))}")
            for item in (item for item in items if item.memory_type == memory_type):
                lines.append(f"- {item.summary} [{item.citation_key}]")
            lines.append("")

        lines.append("## Citations")
        for item in items:
            link = _relative_link_from_synthesis(item.relative_path)
            wikilink = wikilink_for_memory(item.memory_id, item.relative_path)
            source = _source_label(item.source)
            suffix = f"; source: {source}" if source else ""
            lines.append(f"- [{item.citation_key}] {wikilink} ({link}){suffix}")
        lines.append("")

    lines.append("## Next Steps")
    for step in NEXT_STEPS:
        lines.append(f"- {step}")
    return "\n".join(lines).rstrip() + "\n"


def _select_documents(
    documents: Sequence[MemoryDocument],
    *,
    config: MemoryConfig,
    project: Optional[str],
    query: Optional[str],
    limit: int,
) -> tuple[MemoryDocument, ...]:
    candidates = [
        document
        for document in documents
        if document.frontmatter.status == LifecycleStatus.ACTIVE
        and (project is None or document.frontmatter.project == project)
    ]
    if query is None:
        return tuple(sorted(candidates, key=lambda document: _document_sort_key(config, document))[:limit])
    query_terms = _query_terms(query)
    if not query_terms:
        return ()

    scored = [
        (score, document)
        for document in candidates
        if (score := _document_query_score(document, query=query or "", terms=query_terms)) > 0
    ]
    return tuple(
        document
        for _, document in sorted(
            scored,
            key=lambda item: (-item[0], *_document_sort_key(config, item[1])),
        )[:limit]
    )


def _document_sort_key(config: MemoryConfig, document: MemoryDocument) -> tuple[int, str, str]:
    frontmatter = document.frontmatter
    memory_type = frontmatter.type.value
    relative_path = _relative_path(config, document)
    return (
        TYPE_ORDER.index(memory_type) if memory_type in TYPE_ORDER else len(TYPE_ORDER),
        frontmatter.id,
        relative_path.as_posix(),
    )


def _item_from_document(config: MemoryConfig, document: MemoryDocument, *, citation_key: str) -> SynthesisItem:
    frontmatter = document.frontmatter
    return SynthesisItem(
        memory_id=frontmatter.id,
        memory_type=frontmatter.type.value,
        summary=_summary_text(document),
        citation_key=citation_key,
        relative_path=_relative_path(config, document),
        source=_source_from_document(document),
    )


def _summary_text(document: MemoryDocument, *, max_words: int = 32) -> str:
    for observation in document.frontmatter.observations:
        text = _clean_summary_text(observation.text)
        if text:
            return _truncate_words(text, max_words=max_words)
    body_text = _clean_summary_text(document.body)
    if body_text:
        return _truncate_words(body_text, max_words=max_words)
    return document.frontmatter.id


def _document_query_score(document: MemoryDocument, *, query: str, terms: Sequence[str]) -> int:
    haystack = _document_query_haystack(document)
    score = 0
    cleaned_query = query.lower().strip()
    if cleaned_query and cleaned_query in haystack:
        score += 3
    for term in terms:
        if term in haystack:
            score += 1
    return score


def _document_query_haystack(document: MemoryDocument) -> str:
    frontmatter = document.frontmatter
    values: list[str] = [
        frontmatter.id,
        frontmatter.type.value,
        frontmatter.scope.value,
        frontmatter.project or "",
        frontmatter.title or "",
        " ".join(frontmatter.aliases),
        " ".join(frontmatter.tags),
        document.body,
    ]
    values.extend(observation.text for observation in frontmatter.observations)
    if frontmatter.source:
        values.extend(
            str(value)
            for value in (
                frontmatter.source.path,
                frontmatter.source.url,
                frontmatter.source.title,
            )
            if value
        )
    return _clean_summary_text(" ".join(values)).lower()


def _query_terms(query: Optional[str]) -> tuple[str, ...]:
    cleaned = _clean_optional(query)
    if cleaned is None:
        return ()
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.findall(r"[a-z0-9][a-z0-9_.:-]*", cleaned.lower()):
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return tuple(terms)


def _source_from_document(document: MemoryDocument) -> Optional[dict[str, Any]]:
    source = document.frontmatter.source
    if source is None:
        return None
    payload = source.model_dump(mode="json", exclude_none=True)
    return payload if payload else None


def _source_label(source: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not source:
        return None
    title = source.get("title")
    if source.get("path"):
        return wikilink_for_memory(str(title or "Source"), source.get("path"))
    if source.get("url"):
        label = str(title or source["url"])
        return f"[{label}]({source['url']})"
    return None


def _result_filters(*, project: Optional[str], query: Optional[str]) -> dict[str, Any]:
    filters: dict[str, Any] = {"status": LifecycleStatus.ACTIVE.value}
    if project is not None:
        filters["project"] = project
    if query is not None:
        filters["query"] = query
    return filters


def _clean_summary_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned_lines.append(re.sub(r"^[-*]\s+", "", stripped))
    return re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()


def _truncate_words(text: str, *, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(".,;:") + "..."


def _ordered_types(items: Sequence[SynthesisItem]) -> tuple[str, ...]:
    present = {item.memory_type for item in items}
    ordered = [memory_type for memory_type in TYPE_ORDER if memory_type in present]
    ordered.extend(sorted(present - set(TYPE_ORDER)))
    return tuple(ordered)


def _relative_path(config: MemoryConfig, document: MemoryDocument) -> Path:
    if document.path is None:
        return Path(document.frontmatter.id)
    try:
        return document.path.relative_to(config.vault_path)
    except ValueError:
        return document.path


def _relative_link_from_synthesis(relative_path: Path) -> str:
    return f"../{relative_path.as_posix()}"


def _unique_synthesis_path(root: Path, *, generated_at: datetime, title: str) -> Path:
    slug = _slugify(title)
    stem = f"{generated_at:%Y-%m-%d}_{slug}"
    candidate = root / f"{stem}.md"
    suffix = 2
    while candidate.exists():
        candidate = root / f"{stem}-{suffix}.md"
        suffix += 1
    return candidate


def _synthesis_title(*, title: Optional[str], project: Optional[str], query: Optional[str] = None) -> str:
    cleaned = _clean_optional(title)
    if cleaned:
        return cleaned
    if project and query:
        return f"{project} synthesis: {query}"
    if query:
        return f"Synthesis: {query}"
    if project:
        return f"{project} synthesis"
    return "Memory synthesis"


def _synthesis_alias(*, project: Optional[str]) -> str:
    if project:
        return f"Memora Synthesis: {project}"
    return "Memora Synthesis"


def _title_from_type(memory_type: str) -> str:
    return memory_type.replace("_", " ").title()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:72].strip("-") or "synthesis"


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _validate_limit(value: int) -> int:
    limit = int(value)
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return limit


def _normalize_now(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.tzinfo.utcoffset(current) is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


__all__ = [
    "DEFAULT_LIMIT",
    "SYNTHESIS_SCHEMA",
    "SynthesisItem",
    "SynthesisResult",
    "plan_synthesis",
    "render_synthesis_markdown",
    "write_synthesis",
]
