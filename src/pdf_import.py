"""Explicit PDF import helpers for optional source capture."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class PdfImportError(ValueError):
    """Raised when explicit PDF import cannot read or normalize text."""


@dataclass(frozen=True)
class PdfImportContent:
    """Extracted or user-provided PDF text normalized for source capture."""

    path: Path
    content: str
    extract: str
    title: str
    source_kind: str
    extractor: str
    origin: dict[str, str]

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
            "extractor": self.extractor,
            "content_length": self.content_length,
            "extract_length": self.extract_length,
            "origin": dict(self.origin),
        }


def load_pdf_content(path: Path, *, text_file: Optional[Path] = None) -> PdfImportContent:
    """Load text for one explicit local PDF path.

    If ``text_file`` is provided, the PDF path is still recorded as origin, but
    extraction uses the supplied UTF-8 text. Without it, the optional ``pypdf``
    dependency is used when available.
    """

    pdf_path = path.expanduser()
    if not pdf_path.is_file():
        raise PdfImportError(f"PDF file not found: {pdf_path}")

    if text_file is not None:
        selected_text_file = text_file.expanduser()
        if not selected_text_file.is_file():
            raise PdfImportError(f"PDF text file not found: {selected_text_file}")
        text = selected_text_file.read_text(encoding="utf-8")
        extractor = "text_file"
        source_kind = "pre_extracted_text"
        origin = _pdf_origin(pdf_path, extractor=extractor, source_kind=source_kind)
        origin.update(
            {
                "text_file": str(selected_text_file),
                "text_file_name": selected_text_file.name,
            }
        )
    else:
        text = extract_pdf_text(pdf_path)
        extractor = "pypdf"
        source_kind = "pdf_text"
        origin = _pdf_origin(pdf_path, extractor=extractor, source_kind=source_kind)

    normalized = _normalize_text(text)
    if not normalized:
        raise PdfImportError("PDF text is empty; pass --text-file with extracted text if needed")

    title = pdf_path.stem
    return PdfImportContent(
        path=pdf_path,
        content=_source_content(pdf_path, normalized),
        extract=normalized,
        title=title,
        source_kind=source_kind,
        extractor=extractor,
        origin=origin,
    )


def extract_pdf_text(path: Path) -> str:
    """Extract text with the optional pypdf dependency."""

    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PdfImportError(
            "No PDF extractor is available. Install the optional dependency with "
            "`pip install 'memora[pdf]'` or pass --text-file/--content-file "
            "with pre-extracted text."
        ) from exc

    try:
        reader = PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise PdfImportError(f"failed to extract PDF text from {path}: {exc}") from exc
    return "\n\n".join(part for part in parts if part.strip())


def _pdf_origin(path: Path, *, extractor: str, source_kind: str) -> dict[str, str]:
    return {
        "provider": "pdf",
        "path": str(path),
        "file_name": path.name,
        "extractor": extractor,
        "source_kind": source_kind,
        "content_type": "application/pdf",
    }


def _source_content(path: Path, text: str) -> str:
    return f"PDF path: {path}\n\nExtracted text:\n\n{text}"


def _normalize_text(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


__all__ = [
    "PdfImportContent",
    "PdfImportError",
    "extract_pdf_text",
    "load_pdf_content",
]
