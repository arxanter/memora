"""Explicit Zoom summary/transcript import helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ZoomImportError(ValueError):
    """Raised when an explicit Zoom export cannot be read or normalized."""


@dataclass(frozen=True)
class ZoomImportContent:
    """User-exported Zoom meeting material normalized for source capture."""

    path: Path
    content: str
    extract: str
    title: str
    source_kind: str
    origin: dict[str, str]
    meeting: dict[str, object]

    @property
    def content_length(self) -> int:
        return len(self.content)

    @property
    def extract_length(self) -> int:
        return len(self.extract)

    def summary(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "title": self.title,
            "source_kind": self.source_kind,
            "content_length": self.content_length,
            "extract_length": self.extract_length,
            "origin": dict(self.origin),
            "meeting": dict(self.meeting),
        }


def load_zoom_content(
    path: Path,
    *,
    title: Optional[str] = None,
    meeting_date: Optional[str] = None,
    meeting_time: Optional[str] = None,
    meeting_id: Optional[str] = None,
    meeting_url: Optional[str] = None,
) -> ZoomImportContent:
    """Load one explicit local Zoom meeting summary/transcript export."""

    export_path = path.expanduser()
    if not export_path.is_file():
        raise ZoomImportError(f"Zoom export file not found: {export_path}")

    text = export_path.read_text(encoding="utf-8")
    normalized = _normalize_text(text)
    if not normalized:
        raise ZoomImportError("Zoom export text is empty")

    parsed = _parse_meeting_export(normalized)
    selected_title = _clean_string(title) or parsed["title"] or export_path.stem
    selected_meeting = _meeting_metadata(
        parsed,
        title=selected_title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        meeting_id=meeting_id,
        meeting_url=meeting_url,
    )
    source_kind = "markdown_export" if export_path.suffix.lower() in {".md", ".markdown"} else "text_export"
    origin = _origin(export_path, source_kind=source_kind, meeting=selected_meeting)

    return ZoomImportContent(
        path=export_path,
        content=_source_content(export_path, normalized),
        extract=_extract_content(selected_title, normalized, selected_meeting),
        title=selected_title,
        source_kind=source_kind,
        origin=origin,
        meeting=selected_meeting,
    )


def _parse_meeting_export(text: str) -> dict[str, object]:
    lines = text.splitlines()
    return {
        "title": _extract_title(lines),
        "meeting_date": _extract_field(lines, ("date", "meeting date")),
        "meeting_time": _extract_field(lines, ("time", "meeting time", "start time", "started")),
        "meeting_id": _extract_field(lines, ("meeting id", "zoom meeting id")),
        "meeting_url": _extract_url(lines),
        "participants": _extract_people(lines),
        "action_items": _extract_action_items(lines),
    }


def _meeting_metadata(
    parsed: dict[str, object],
    *,
    title: str,
    meeting_date: Optional[str],
    meeting_time: Optional[str],
    meeting_id: Optional[str],
    meeting_url: Optional[str],
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "title": title,
        "meeting_date": _clean_string(meeting_date) or parsed.get("meeting_date"),
        "meeting_time": _clean_string(meeting_time) or parsed.get("meeting_time"),
        "meeting_id": _clean_string(meeting_id) or parsed.get("meeting_id"),
        "meeting_url": _clean_string(meeting_url) or parsed.get("meeting_url"),
        "participants": parsed.get("participants") or [],
        "action_items": parsed.get("action_items") or [],
    }
    return {key: value for key, value in metadata.items() if value}


def _origin(path: Path, *, source_kind: str, meeting: dict[str, object]) -> dict[str, str]:
    origin = {
        "provider": "zoom",
        "path": str(path),
        "file_name": path.name,
        "source_kind": source_kind,
        "content_type": "text/markdown" if source_kind == "markdown_export" else "text/plain",
    }
    for key in ("meeting_date", "meeting_time", "meeting_id", "meeting_url"):
        value = _clean_string(meeting.get(key))
        if value:
            origin[key] = value
    participants = _string_list(meeting.get("participants"))
    if participants:
        origin["participants"] = "; ".join(participants)
    action_items = _string_list(meeting.get("action_items"))
    if action_items:
        origin["action_items"] = "; ".join(action_items)
    return origin


def _source_content(path: Path, text: str) -> str:
    return f"Zoom export path: {path}\n\nExported meeting summary/transcript:\n\n{text}"


def _extract_content(title: str, text: str, meeting: dict[str, object]) -> str:
    parts = [f"# Meeting Source: {title}"]
    metadata_lines = []
    for label, key in (
        ("Meeting date", "meeting_date"),
        ("Meeting time", "meeting_time"),
        ("Meeting ID", "meeting_id"),
        ("Meeting URL", "meeting_url"),
    ):
        value = _clean_string(meeting.get(key))
        if value:
            metadata_lines.append(f"- {label}: {value}")
    participants = _string_list(meeting.get("participants"))
    if participants:
        metadata_lines.append(f"- Participants: {'; '.join(participants)}")
    if metadata_lines:
        parts.append("## Meeting Metadata\n\n" + "\n".join(metadata_lines))

    action_items = _string_list(meeting.get("action_items"))
    if action_items:
        parts.append("## Parsed Action Items\n\n" + "\n".join(f"- {item}" for item in action_items))

    parts.append("## Exported Summary Or Transcript\n\n" + text)
    return "\n\n".join(parts)


def _extract_title(lines: list[str]) -> Optional[str]:
    for line in lines:
        heading = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if heading:
            return _clean_string(heading.group(1))
    return _extract_field(lines, ("title", "topic", "meeting", "meeting summary"))


def _extract_field(lines: list[str], names: tuple[str, ...]) -> Optional[str]:
    field_names = "|".join(re.escape(name) for name in names)
    pattern = re.compile(rf"^\s*(?:[-*]\s*)?(?:{field_names})\s*:\s*(.+?)\s*$", re.IGNORECASE)
    for line in lines:
        match = pattern.match(line)
        if match:
            return _clean_string(match.group(1))
    return None


def _extract_url(lines: list[str]) -> Optional[str]:
    explicit = _extract_field(lines, ("meeting url", "zoom url", "join url", "url"))
    if explicit:
        return explicit
    for line in lines:
        match = re.search(r"https?://\S*zoom\.us/\S+", line)
        if match:
            return match.group(0).rstrip(").,")
    return None


def _extract_people(lines: list[str]) -> list[str]:
    inline = _extract_field(lines, ("participants", "attendees"))
    if inline:
        return _split_inline_list(inline)
    return _extract_section_items(lines, {"participants", "attendees"})


def _extract_action_items(lines: list[str]) -> list[str]:
    inline = _extract_field(lines, ("action items", "actions", "next steps", "todos", "tasks"))
    if inline:
        return _split_inline_list(inline)
    return _extract_section_items(lines, {"action items", "actions", "next steps", "todos", "tasks"})


def _extract_section_items(lines: list[str], headings: set[str], *, limit: int = 20) -> list[str]:
    items: list[str] = []
    in_section = False
    for line in lines:
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if heading:
            normalized_heading = _normalize_heading(heading.group(1))
            if in_section and normalized_heading not in headings:
                break
            in_section = normalized_heading in headings
            continue
        if not in_section:
            continue
        cleaned = _clean_section_item(line)
        if cleaned:
            items.append(cleaned)
            if len(items) >= limit:
                break
        elif items and not line.strip():
            break
    return items


def _clean_section_item(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped:
        return None
    stripped = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped)
    return _clean_string(stripped)


def _split_inline_list(value: str) -> list[str]:
    return [item for item in (_clean_string(part) for part in re.split(r"[,;\n]+", value)) if item]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_clean_string(part) for part in value) if item]


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _clean_string(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _normalize_text(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


__all__ = [
    "ZoomImportContent",
    "ZoomImportError",
    "load_zoom_content",
]
