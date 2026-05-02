"""Small Markdown presentation helpers for Obsidian-friendly output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union


PathLike = Union[Path, str]


def readable_title(text: str, *, fallback: str, max_words: int = 10) -> str:
    """Return a compact, human-readable note title without changing canonical ids."""

    cleaned = _clean_title_text(text)
    if not cleaned:
        return fallback
    words = cleaned.split()
    if len(words) > max_words:
        cleaned = " ".join(words[:max_words]).rstrip(".,;:") + "..."
    return cleaned or fallback


def aliases(*values: Optional[str]) -> list[str]:
    """Return de-duplicated aliases suitable for Obsidian frontmatter."""

    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_alias(value)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        selected.append(cleaned)
    return selected


def wikilink(target: str, *, label: Optional[str] = None) -> str:
    """Render an Obsidian wikilink with conservative escaping for generated labels."""

    cleaned_target = _clean_link_part(target)
    cleaned_label = _clean_link_part(label) if label else None
    if cleaned_label and cleaned_label != cleaned_target:
        return f"[[{cleaned_target}|{cleaned_label}]]"
    return f"[[{cleaned_target}]]"


def wikilink_for_path(path: PathLike, *, label: Optional[str] = None) -> str:
    """Render a vault-relative Markdown path as an Obsidian wikilink target."""

    target = Path(path).as_posix()
    if target.endswith(".md"):
        target = target[:-3]
    return wikilink(target, label=label)


def wikilink_for_memory(memory_id: str, path: Optional[PathLike] = None) -> str:
    """Link to a memory path when known, otherwise to its stable id alias."""

    if path is None:
        return wikilink(memory_id)
    return wikilink_for_path(path, label=memory_id)


def _clean_title_text(text: str) -> str:
    lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"---", "..."}:
            continue
        stripped = re.sub(r"^#{1,6}\s+", "", stripped)
        stripped = re.sub(r"^[-*]\s+", "", stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            lines.append(stripped)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _clean_alias(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _clean_link_part(value: Optional[str]) -> str:
    cleaned = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    cleaned = cleaned.replace("[", "").replace("]", "").replace("|", "-")
    return cleaned or "Untitled"
