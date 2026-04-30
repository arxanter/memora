"""Source material capture helpers for agent-driven ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from agent_memory.config import MemoryConfig
from agent_memory.sync import atomic_write_many, vault_lock

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


def _clean_list(values: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = _optional_string(str(value))
        if item:
            cleaned.append(item)
    return cleaned


__all__ = [
    "SourceCaptureResult",
    "save_source_material",
]
