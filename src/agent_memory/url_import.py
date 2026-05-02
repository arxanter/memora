"""Explicit URL import helpers for CLI-first source capture."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

MAX_URL_IMPORT_BYTES = 2_000_000
URL_IMPORT_USER_AGENT = "agent-memory/0.1"


class UrlImportError(ValueError):
    """Raised when explicit URL import cannot read or normalize content."""


@dataclass(frozen=True)
class UrlImportContent:
    """Fetched or user-provided URL content normalized for source capture."""

    url: str
    content: str
    extract: str
    title: Optional[str]
    content_type: str
    source_kind: str
    origin: dict[str, str]

    @property
    def content_length(self) -> int:
        return len(self.content)

    @property
    def extract_length(self) -> int:
        return len(self.extract)

    def summary(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "content_type": self.content_type,
            "source_kind": self.source_kind,
            "content_length": self.content_length,
            "extract_length": self.extract_length,
            "origin": dict(self.origin),
        }


def normalize_url(url: str) -> str:
    """Validate an explicit import URL and return its normalized string form."""

    cleaned = str(url or "").strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UrlImportError("URL must be an absolute http(s) URL")
    return urllib.parse.urlunparse(parsed)


def fetch_url_content(url: str, *, timeout: float = 15.0) -> UrlImportContent:
    """Fetch URL content with the standard library and normalize it."""

    normalized_url = normalize_url(url)
    request = urllib.request.Request(
        normalized_url,
        headers={"User-Agent": URL_IMPORT_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and int(status) >= 400:
                raise UrlImportError(f"URL returned HTTP status {status}")
            headers = response.headers
            content_type = headers.get("Content-Type", "application/octet-stream")
            charset = headers.get_content_charset() or "utf-8"
            raw_bytes = response.read(MAX_URL_IMPORT_BYTES + 1)
    except UrlImportError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise UrlImportError(f"failed to fetch URL: {exc}") from exc

    if len(raw_bytes) > MAX_URL_IMPORT_BYTES:
        raise UrlImportError(f"URL content exceeds {MAX_URL_IMPORT_BYTES} bytes")

    content = raw_bytes.decode(charset, errors="replace")
    return parse_url_content(
        normalized_url,
        content,
        content_type=content_type,
        origin={
            "provider": "url",
            "fetcher": "stdlib",
            "url": normalized_url,
            "content_type": content_type,
        },
    )


def load_url_content_file(url: str, path: Path) -> UrlImportContent:
    """Load explicitly saved URL content from a local file while preserving origin URL."""

    normalized_url = normalize_url(url)
    content_path = path.expanduser()
    if not content_path.is_file():
        raise UrlImportError(f"content file not found: {content_path}")
    content = content_path.read_text(encoding="utf-8")
    content_type = "text/html" if content_path.suffix.lower() in {".html", ".htm"} else "text/plain"
    return parse_url_content(
        normalized_url,
        content,
        content_type=content_type,
        origin={
            "provider": "url",
            "fetcher": "from_file",
            "url": normalized_url,
            "content_file": str(content_path),
            "file_name": content_path.name,
            "content_type": content_type,
        },
    )


def parse_url_content(
    url: str,
    content: str,
    *,
    content_type: str,
    origin: dict[str, str],
) -> UrlImportContent:
    """Normalize raw URL content into source text plus a readable extract."""

    normalized_url = normalize_url(url)
    source_kind = "html" if _looks_like_html(content, content_type) else "text"
    if source_kind == "html":
        extract, title = html_to_text(content)
    else:
        extract = _normalize_text(content)
        title = None
    if not content.strip():
        raise UrlImportError("URL content is empty")
    if not extract:
        extract = _normalize_text(content)
    if not extract:
        raise UrlImportError("URL content has no readable text")
    selected_origin = {key: value for key, value in origin.items() if value}
    selected_origin["source_kind"] = source_kind
    return UrlImportContent(
        url=normalized_url,
        content=content,
        extract=extract,
        title=title,
        content_type=content_type,
        source_kind=source_kind,
        origin=selected_origin,
    )


def html_to_text(content: str) -> tuple[str, Optional[str]]:
    """Convert simple HTML into readable plain text without external dependencies."""

    parser = _ReadableHTMLParser()
    parser.feed(content)
    parser.close()
    return _normalize_text("".join(parser.parts)), _normalize_text(" ".join(parser.title_parts)) or None


def _looks_like_html(content: str, content_type: str) -> bool:
    if "html" in (content_type or "").lower():
        return True
    return bool(re.search(r"<(?:!doctype\s+html|html|head|title|body|article|main|p|h[1-6])\b", content, re.I))


def _normalize_text(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _ReadableHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"canvas", "script", "style", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower()
        if tag_name in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag_name == "title":
            self._in_title = True
        if tag_name == "li":
            self.parts.append("\n- ")
        elif tag_name in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag_name == "title":
            self._in_title = False
        if tag_name in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if data.strip():
            self.parts.append(data)


__all__ = [
    "UrlImportContent",
    "UrlImportError",
    "fetch_url_content",
    "html_to_text",
    "load_url_content_file",
    "normalize_url",
    "parse_url_content",
]
