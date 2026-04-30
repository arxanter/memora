"""Source material capture helpers for agent-driven ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

import yaml

from agent_memory.config import MemoryConfig
from agent_memory.schema import AuthorKind, LifecycleStatus, MemoryScope, MemoryType, SourceRef
from agent_memory.sync import atomic_write_many, vault_lock
from agent_memory.vault import RememberResult, remember_memory

PathLike = Union[Path, str]


@dataclass(frozen=True)
class SourceCaptureResult:
    """Saved raw source material and optional agent-created extract."""

    source_id: str
    source_dir: Path
    relative_dir: Path
    source_path: Path
    relative_source_path: Path
    extract_path: Optional[Path]
    relative_extract_path: Optional[Path]
    url: Optional[str]
    title: str
    project: Optional[str]
    tags: tuple[str, ...]

    @property
    def citations(self) -> list[dict[str, str]]:
        citations = [
            {
                "id": self.source_id,
                "path": self.relative_source_path.as_posix(),
                "kind": "source",
            }
        ]
        if self.relative_extract_path is not None:
            citations.append(
                {
                    "id": self.source_id,
                    "path": self.relative_extract_path.as_posix(),
                    "kind": "source_extract",
                }
            )
        return citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "source_id": self.source_id,
            "source_dir": str(self.source_dir),
            "relative_dir": self.relative_dir.as_posix(),
            "source_path": str(self.source_path),
            "relative_source_path": self.relative_source_path.as_posix(),
            "extract_path": str(self.extract_path) if self.extract_path is not None else None,
            "relative_extract_path": (
                self.relative_extract_path.as_posix() if self.relative_extract_path is not None else None
            ),
            "url": self.url,
            "title": self.title,
            "project": self.project,
            "tags": list(self.tags),
            "citations": self.citations,
        }


@dataclass(frozen=True)
class PromotedMemoryResult:
    """One pending memory promoted from a saved source extract."""

    result: RememberResult
    source: SourceRef
    confidence: float
    author_name: str

    @property
    def citation(self) -> dict[str, str]:
        return {
            "id": self.result.memory_id,
            "path": self.result.relative_path.as_posix(),
            "kind": "memory",
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.result.to_dict()
        payload.update(
            {
                "review_required": self.result.status == LifecycleStatus.PENDING,
                "author": {"kind": AuthorKind.AGENT.value, "name": self.author_name},
                "confidence": self.confidence,
                "source": self.source.model_dump(mode="json", exclude_none=True),
                "citations": [self.citation],
            }
        )
        return payload


@dataclass(frozen=True)
class SourcePromotionResult:
    """Saved source material plus pending atomic memories linked to it."""

    source: SourceCaptureResult
    memories: tuple[PromotedMemoryResult, ...]

    @property
    def citations(self) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for citation in [*self.source.citations, *(memory.citation for memory in self.memories)]:
            signature = (citation["kind"], citation["id"], citation["path"])
            if signature in seen:
                continue
            seen.add(signature)
            citations.append(citation)
        return citations

    def to_dict(self) -> dict[str, Any]:
        pending_count = sum(
            1
            for memory in self.memories
            if memory.result.status == LifecycleStatus.PENDING
        )
        return {
            "ok": True,
            "implemented": True,
            "source": self.source.to_dict(),
            "memory_count": len(self.memories),
            "pending_count": pending_count,
            "review_required": pending_count > 0,
            "memories": [memory.to_dict() for memory in self.memories],
            "citations": self.citations,
            "next_steps": [
                "Review the saved source and extract under Sources/.",
                "Review the pending atomic memories before approving them.",
            ],
        }


@dataclass(frozen=True)
class _PlannedMemory:
    memory_type: MemoryType
    text: str
    scope: Optional[MemoryScope]
    project: Optional[str]
    tags: tuple[str, ...]
    confidence: float


_PROMOTABLE_MEMORY_TYPES = {
    MemoryType.FACT,
    MemoryType.PREFERENCE,
    MemoryType.DECISION,
    MemoryType.TASK,
    MemoryType.PROJECT_CONTEXT,
}


def save_source_material(
    config: MemoryConfig,
    *,
    title: Optional[str] = None,
    url: Optional[str] = None,
    content: Optional[str] = None,
    extract: Optional[str] = None,
    project: Optional[str] = None,
    tags: Iterable[str] = (),
    slug: Optional[str] = None,
    captured_at: Optional[datetime] = None,
) -> SourceCaptureResult:
    """Save raw material under Sources without promoting it to canonical memory."""

    selected_at = captured_at or datetime.now(timezone.utc).astimezone()
    selected_title = _clean_title(title) or _title_from_url(url) or "Untitled source"
    selected_tags = tuple(_clean_list(tags))
    selected_slug = _slugify(slug or selected_title or url or "source")
    source_id = f"{selected_at:%Y-%m-%d}_{selected_slug}"
    sources_root = config.vault_path / config.sources_dir
    source_dir = _unique_source_dir(sources_root, source_id)
    source_id = source_dir.name

    source_markdown = _render_source_markdown(
        source_id=source_id,
        title=selected_title,
        url=_optional_string(url),
        content=_optional_string(content),
        project=_optional_string(project),
        tags=selected_tags,
        captured_at=selected_at,
    )
    files: list[tuple[PathLike, str]] = [(source_dir / "source.md", source_markdown)]

    extract_path: Optional[Path] = None
    if _optional_string(extract):
        extract_path = source_dir / "extract.md"
        files.append(
            (
                extract_path,
                _render_extract_markdown(
                    source_id=source_id,
                    title=selected_title,
                    url=_optional_string(url),
                    extract=str(extract).strip(),
                    project=_optional_string(project),
                    tags=selected_tags,
                    captured_at=selected_at,
                ),
            )
        )

    with vault_lock(config, name="source-write"):
        atomic_write_many(files)

    return SourceCaptureResult(
        source_id=source_id,
        source_dir=source_dir,
        relative_dir=source_dir.relative_to(config.vault_path),
        source_path=source_dir / "source.md",
        relative_source_path=(source_dir / "source.md").relative_to(config.vault_path),
        extract_path=extract_path,
        relative_extract_path=extract_path.relative_to(config.vault_path) if extract_path else None,
        url=_optional_string(url),
        title=selected_title,
        project=_optional_string(project),
        tags=selected_tags,
    )


def save_source_with_memories(
    config: MemoryConfig,
    *,
    source: Mapping[str, Any],
    memories: Iterable[Mapping[str, Any]],
    author_name: str = "MCP agent",
) -> SourcePromotionResult:
    """Save source material and promote agent-supplied atomic memories for review."""

    source_payload = dict(source)
    planned = tuple(
        _plan_promoted_memory(memory, default_project=_optional_string(source_payload.get("project")))
        for memory in memories
    )
    if not planned:
        raise ValueError("memories must include at least one durable atomic item")

    saved_source = save_source_material(
        config,
        title=_optional_string(source_payload.get("title")),
        url=_optional_string(source_payload.get("url")),
        content=_optional_string(
            source_payload.get("content")
            or source_payload.get("raw")
            or source_payload.get("markdown")
        ),
        extract=_optional_string(source_payload.get("extract") or source_payload.get("summary")),
        project=_optional_string(source_payload.get("project")),
        tags=_clean_list(source_payload.get("tags", ())),
        slug=_optional_string(source_payload.get("slug")),
    )
    source_ref = _source_ref_for_promotion(saved_source)
    promoted: list[PromotedMemoryResult] = []
    for item in planned:
        result = remember_memory(
            config,
            memory_type=item.memory_type,
            text=item.text,
            scope=item.scope,
            project=item.project,
            status=LifecycleStatus.PENDING,
            tags=item.tags,
            author_kind=AuthorKind.AGENT,
            author_name=author_name,
            source=source_ref,
            confidence=item.confidence,
        )
        promoted.append(
            PromotedMemoryResult(
                result=result,
                source=source_ref,
                confidence=item.confidence,
                author_name=author_name,
            )
        )

    return SourcePromotionResult(source=saved_source, memories=tuple(promoted))


def _render_source_markdown(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    content: Optional[str],
    project: Optional[str],
    tags: tuple[str, ...],
    captured_at: datetime,
) -> str:
    frontmatter = _frontmatter(
        source_id=source_id,
        title=title,
        url=url,
        project=project,
        tags=tags,
        captured_at=captured_at,
        kind="source",
    )
    body = content or (
        "No raw content was provided to Agent Memory. The agent should fetch or "
        "read the URL externally, then call save_source again with content and an extract."
    )
    return f"---\n{frontmatter}\n---\n\n# {title}\n\n{_source_url_line(url)}{body.strip()}\n"


def _render_extract_markdown(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    extract: str,
    project: Optional[str],
    tags: tuple[str, ...],
    captured_at: datetime,
) -> str:
    frontmatter = _frontmatter(
        source_id=source_id,
        title=title,
        url=url,
        project=project,
        tags=tags,
        captured_at=captured_at,
        kind="extract",
    )
    return f"---\n{frontmatter}\n---\n\n# Extract: {title}\n\n{_source_url_line(url)}{extract.strip()}\n"


def _frontmatter(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    project: Optional[str],
    tags: tuple[str, ...],
    captured_at: datetime,
    kind: str,
) -> str:
    data = {
        "source_id": source_id,
        "kind": kind,
        "title": title,
        "url": url,
        "project": project,
        "tags": list(tags),
        "captured_at": captured_at.isoformat(),
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False).strip()


def _source_url_line(url: Optional[str]) -> str:
    if not url:
        return ""
    return f"Source URL: {url}\n\n"


def _unique_source_dir(root: Path, source_id: str) -> Path:
    candidate = root / source_id
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = root / f"{source_id}-{index}"
        if not candidate.exists():
            return candidate
    raise ValueError(f"could not allocate unique source directory for {source_id}")


def _clean_title(value: Optional[str]) -> Optional[str]:
    cleaned = _optional_string(value)
    if cleaned is None:
        return None
    return re.sub(r"\s+", " ", cleaned)


def _title_from_url(url: Optional[str]) -> Optional[str]:
    cleaned = _optional_string(url)
    if cleaned is None:
        return None
    without_scheme = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", cleaned)
    return without_scheme.strip("/") or cleaned


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64].strip("-") or "source"


def _optional_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clean_list(values: Optional[Iterable[str]]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    cleaned: list[str] = []
    for value in values:
        item = _optional_string(str(value))
        if item:
            cleaned.append(item)
    return cleaned


def _plan_promoted_memory(
    memory: Mapping[str, Any],
    *,
    default_project: Optional[str],
) -> _PlannedMemory:
    memory_type = MemoryType(memory.get("type", MemoryType.FACT.value))
    if memory_type not in _PROMOTABLE_MEMORY_TYPES:
        allowed = ", ".join(sorted(memory_type.value for memory_type in _PROMOTABLE_MEMORY_TYPES))
        raise ValueError(
            f"source promotion only supports durable atomic memory types: {allowed}"
        )

    confidence = float(memory.get("confidence", 0.5))
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")

    return _PlannedMemory(
        memory_type=memory_type,
        text=_memory_text(memory),
        scope=_optional_enum(MemoryScope, memory.get("scope")),
        project=_optional_string(memory.get("project")) or default_project,
        tags=tuple(_clean_list(memory.get("tags", ()))),
        confidence=confidence,
    )


def _memory_text(memory: Mapping[str, Any]) -> str:
    for key in ("text", "body", "content"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("memory must include non-empty text")


def _optional_enum(enum_type: Any, value: Any) -> Any:
    if value in (None, ""):
        return None
    return enum_type(value)


def _source_ref_for_promotion(source: SourceCaptureResult) -> SourceRef:
    relative_path = source.relative_extract_path or source.relative_source_path
    return SourceRef(
        path=relative_path.as_posix(),
        url=source.url,
        title=source.title,
        source_id=source.source_id,
    )


__all__ = [
    "PromotedMemoryResult",
    "SourceCaptureResult",
    "SourcePromotionResult",
    "save_source_material",
    "save_source_with_memories",
]
