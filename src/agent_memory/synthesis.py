"""Deterministic synthesis output for active canonical memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from agent_memory.config import MemoryConfig
from agent_memory.markdown import aliases as presentation_aliases
from agent_memory.markdown import wikilink_for_memory
from agent_memory.schema import LifecycleStatus, MemoryDocument, MemoryType, validate_vault
from agent_memory.sync import atomic_write_text, vault_lock

SYNTHESIS_SCHEMA = "agent-memory.synthesis.v1"
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
    "Promote durable conclusions with `memory remember`; synthesis does not create canonical memories.",
)


@dataclass(frozen=True)
class SynthesisItem:
    """One selected memory rendered as a synthesis bullet."""

    memory_id: str
    memory_type: str
    summary: str
    citation_key: str
    relative_path: Path

    def citation(self) -> dict[str, Any]:
        return {
            "key": self.citation_key,
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "type": self.memory_type,
        }


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
            "generated_at": self.generated_at.isoformat(),
            "vault_path": str(self.config.vault_path),
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "memory_count": len(self.items),
            "source_memory_ids": [item.memory_id for item in self.items],
            "citations": [dict(citation) for citation in self.citations],
            "next_steps": list(NEXT_STEPS),
        }


def write_synthesis(
    config: MemoryConfig,
    *,
    project: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    now: Optional[datetime] = None,
) -> SynthesisResult:
    """Write a deterministic Markdown synthesis under the vault's Synthesis directory."""

    selected_limit = _validate_limit(limit)
    selected_project = _clean_optional(project)
    selected_title = _synthesis_title(title=title, project=selected_project)
    generated_at = _normalize_now(now)

    report = validate_vault(config.vault_path)
    if report.issues:
        first_issue = report.issues[0]
        raise ValueError(f"cannot synthesize invalid vault: {first_issue.path}: {first_issue.message}")

    documents = _select_documents(
        report.documents,
        config=config,
        project=selected_project,
        limit=selected_limit,
    )
    items = tuple(
        _item_from_document(config, document, citation_key=f"C{position}")
        for position, document in enumerate(documents, start=1)
    )
    markdown = render_synthesis_markdown(
        title=selected_title,
        project=selected_project,
        generated_at=generated_at,
        items=items,
    )

    with vault_lock(config, name="synthesis"):
        synthesis_root = config.vault_path / config.synthesis_dir
        synthesis_root.mkdir(parents=True, exist_ok=True)
        target_path = _unique_synthesis_path(synthesis_root, generated_at=generated_at, title=selected_title)
        atomic_write_text(target_path, markdown)

    return SynthesisResult(
        config=config,
        path=target_path,
        relative_path=target_path.relative_to(config.vault_path),
        title=selected_title,
        project=selected_project,
        generated_at=generated_at,
        markdown=markdown,
        items=items,
    )


def render_synthesis_markdown(
    *,
    title: str,
    project: Optional[str],
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
        "generated_at": generated_at.isoformat(),
        "source_memory_ids": [item.memory_id for item in items],
        "source_memory_count": len(items),
        "generated_by": "memory synthesize",
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
            lines.append(f"- [{item.citation_key}] {wikilink} ({link})")
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
    limit: int,
) -> tuple[MemoryDocument, ...]:
    selected = [
        document
        for document in documents
        if document.frontmatter.status == LifecycleStatus.ACTIVE
        and (project is None or document.frontmatter.project == project)
    ]
    return tuple(sorted(selected, key=lambda document: _document_sort_key(config, document))[:limit])


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


def _synthesis_title(*, title: Optional[str], project: Optional[str]) -> str:
    cleaned = _clean_optional(title)
    if cleaned:
        return cleaned
    if project:
        return f"{project} synthesis"
    return "Memory synthesis"


def _synthesis_alias(*, project: Optional[str]) -> str:
    if project:
        return f"Agent Memory Synthesis: {project}"
    return "Agent Memory Synthesis"


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
    "render_synthesis_markdown",
    "write_synthesis",
]
