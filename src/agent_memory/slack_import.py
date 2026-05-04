"""Explicit Slack thread/message export import helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class SlackImportError(ValueError):
    """Raised when an explicit Slack export cannot be read or normalized."""


@dataclass(frozen=True)
class SlackImportContent:
    """User-exported Slack thread/message material normalized for source capture."""

    path: Path
    content: str
    extract: str
    title: str
    source_kind: str
    origin: dict[str, str]
    thread: dict[str, object]

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
            "thread": dict(self.thread),
        }


def load_slack_content(
    path: Path,
    *,
    title: Optional[str] = None,
    channel: Optional[str] = None,
    thread_ts: Optional[str] = None,
    permalink: Optional[str] = None,
) -> SlackImportContent:
    """Load one explicit local Slack thread/message export.

    This intentionally does not call Slack APIs. It only normalizes the user
    supplied local file into reviewable source content.
    """

    export_path = path.expanduser()
    if not export_path.is_file():
        raise SlackImportError(f"Slack export file not found: {export_path}")

    raw_text = export_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise SlackImportError("Slack export text is empty")

    parsed = _parse_export(raw_text, suffix=export_path.suffix.lower())
    selected_thread = _thread_metadata(
        parsed,
        channel=channel,
        thread_ts=thread_ts,
        permalink=permalink,
    )
    selected_title = _clean_string(title) or _default_title(export_path, selected_thread)
    origin = _origin(export_path, source_kind=parsed["source_kind"], thread=selected_thread)
    readable_text = str(parsed["text"])

    return SlackImportContent(
        path=export_path,
        content=_source_content(export_path, readable_text),
        extract=_extract_content(selected_title, readable_text, selected_thread),
        title=selected_title,
        source_kind=str(parsed["source_kind"]),
        origin=origin,
        thread=selected_thread,
    )


def _parse_export(raw_text: str, *, suffix: str) -> dict[str, object]:
    if suffix == ".json":
        parsed = _parse_json_export(raw_text)
        if parsed is not None:
            return parsed
    source_kind = "markdown_export" if suffix in {".md", ".markdown"} else "text_export"
    return {
        "source_kind": source_kind,
        "text": _normalize_text(raw_text),
        "message_count": None,
        "channel": None,
        "thread_ts": None,
        "permalink": None,
    }


def _parse_json_export(raw_text: str) -> Optional[dict[str, object]]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    messages, metadata = _messages_from_payload(payload)
    if not messages:
        return None

    lines: list[str] = []
    message_count = 0
    for message in messages:
        rendered, rendered_count = _render_message(message, indent=0)
        if rendered:
            lines.extend(rendered)
            message_count += rendered_count

    text = "\n".join(lines).strip()
    if not text:
        return None

    first_message = messages[0] if isinstance(messages[0], dict) else {}
    return {
        "source_kind": "json_export",
        "text": text,
        "message_count": message_count,
        "channel": _clean_string(metadata.get("channel") or metadata.get("channel_name")),
        "thread_ts": _clean_string(
            metadata.get("thread_ts")
            or (first_message.get("thread_ts") if isinstance(first_message, dict) else None)
            or (first_message.get("ts") if isinstance(first_message, dict) else None)
        ),
        "permalink": _clean_string(metadata.get("permalink") or metadata.get("url")),
    }


def _messages_from_payload(payload: object) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], {}
    if not isinstance(payload, dict):
        return [], {}

    if isinstance(payload.get("messages"), list):
        return [item for item in payload["messages"] if isinstance(item, dict)], payload

    message = payload.get("message")
    if isinstance(message, dict):
        root = dict(message)
        replies = payload.get("replies")
        if isinstance(replies, list):
            root["replies"] = replies
        return [root], payload

    if "text" in payload or "ts" in payload:
        return [payload], payload
    return [], payload


def _render_message(message: dict[str, Any], *, indent: int) -> tuple[list[str], int]:
    user = _clean_string(
        message.get("user_name")
        or message.get("username")
        or message.get("real_name")
        or message.get("name")
        or message.get("user")
        or message.get("bot_id")
    ) or "unknown"
    ts = _clean_string(message.get("ts") or message.get("timestamp") or message.get("time"))
    text = _message_text(message)
    prefix = "  " * indent + "- "
    if ts:
        line = f"{prefix}[{ts}] {user}: {text}"
    else:
        line = f"{prefix}{user}: {text}"
    lines = [line]
    count = 1
    replies = message.get("replies")
    if isinstance(replies, list):
        for reply in replies:
            if not isinstance(reply, dict):
                continue
            rendered, rendered_count = _render_message(reply, indent=indent + 1)
            lines.extend(rendered)
            count += rendered_count
    return lines, count


def _message_text(message: dict[str, Any]) -> str:
    text = _clean_string(message.get("text"))
    if text:
        return text
    attachments = message.get("attachments")
    if isinstance(attachments, list):
        parts = [
            _clean_string(item.get("text") or item.get("title"))
            for item in attachments
            if isinstance(item, dict)
        ]
        cleaned_parts = [part for part in parts if part]
        if cleaned_parts:
            return " ".join(cleaned_parts)
    blocks = message.get("blocks")
    if isinstance(blocks, list):
        parts = [
            _clean_string(_nested_text(item))
            for item in blocks
            if isinstance(item, dict)
        ]
        cleaned_parts = [part for part in parts if part]
        if cleaned_parts:
            return " ".join(cleaned_parts)
    return ""


def _nested_text(value: object) -> Optional[str]:
    if isinstance(value, dict):
        text = _clean_string(value.get("text"))
        if text:
            return text
        parts = [_nested_text(item) for item in value.values()]
        return " ".join(part for part in parts if part) or None
    if isinstance(value, list):
        parts = [_nested_text(item) for item in value]
        return " ".join(part for part in parts if part) or None
    return None


def _thread_metadata(
    parsed: dict[str, object],
    *,
    channel: Optional[str],
    thread_ts: Optional[str],
    permalink: Optional[str],
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "channel": _clean_string(channel) or _clean_string(parsed.get("channel")),
        "thread_ts": _clean_string(thread_ts) or _clean_string(parsed.get("thread_ts")),
        "permalink": _clean_string(permalink) or _clean_string(parsed.get("permalink")),
        "message_count": parsed.get("message_count"),
    }
    return {key: value for key, value in metadata.items() if value}


def _origin(path: Path, *, source_kind: object, thread: dict[str, object]) -> dict[str, str]:
    content_type = "application/json" if source_kind == "json_export" else _text_content_type(path)
    origin = {
        "provider": "slack",
        "path": str(path),
        "file_name": path.name,
        "source_kind": str(source_kind),
        "content_type": content_type,
    }
    for key in ("channel", "thread_ts", "permalink", "message_count"):
        value = _clean_string(thread.get(key))
        if value:
            origin[key] = value
    return origin


def _source_content(path: Path, text: str) -> str:
    return f"Slack export path: {path}\n\nExported Slack thread/message:\n\n{text}"


def _extract_content(title: str, text: str, thread: dict[str, object]) -> str:
    parts = [f"# Slack Source: {title}"]
    metadata_lines = []
    for label, key in (
        ("Channel", "channel"),
        ("Thread timestamp", "thread_ts"),
        ("Permalink", "permalink"),
        ("Message count", "message_count"),
    ):
        value = _clean_string(thread.get(key))
        if value:
            metadata_lines.append(f"- {label}: {value}")
    if metadata_lines:
        parts.append("## Slack Metadata\n\n" + "\n".join(metadata_lines))
    parts.append("## Exported Thread Or Message\n\n" + text)
    return "\n\n".join(parts)


def _default_title(path: Path, thread: dict[str, object]) -> str:
    channel = _clean_string(thread.get("channel"))
    thread_ts = _clean_string(thread.get("thread_ts"))
    if channel and thread_ts:
        return f"Slack thread {channel} {thread_ts}"
    if channel:
        return f"Slack export {channel}"
    if thread_ts:
        return f"Slack thread {thread_ts}"
    return path.stem


def _text_content_type(path: Path) -> str:
    return "text/markdown" if path.suffix.lower() in {".md", ".markdown"} else "text/plain"


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
    "SlackImportContent",
    "SlackImportError",
    "load_slack_content",
]
