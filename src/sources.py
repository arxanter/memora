"""Source material capture helpers for agent-driven ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

import yaml

from config import AgentPolicyConfig, AgentTrustLevel, MemoryConfig
from indexer import estimate_tokens
from safety import (
    SafetyScanResult,
    merge_scan_results,
    normalize_risk_flags,
    scan_metadata,
    scan_source_material,
    scan_text,
)
from schema import AuthorKind, LifecycleStatus, MemoryScope, MemoryType, SourceRef
from session import normalize_session_recall_state, session_trace
from sync import atomic_write_many, vault_lock
from vault import RememberResult, remember_memory

PathLike = Union[Path, str]
SOURCE_CHANNELS = {
    "manual",
    "url",
    "file",
    "pdf",
    "ai_session",
    "web_clipper",
    "zoom",
    "slack",
}
SOURCE_QUALITIES = {
    "explicit_user",
    "user_provided",
    "agent_fetched",
    "meeting_summary",
    "chat_thread",
    "imported_export",
    "unknown",
}
SOURCE_SENSITIVITIES = {"normal", "private", "secret", "unsafe"}
PROMOTION_BLOCKED_SENSITIVITIES = {"secret", "unsafe"}
_SCHEDULED_CHANNEL_RE = re.compile(r"^scheduled_[a-z0-9_]{1,64}$")


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
    tags: tuple[str, ...]
    channel: str
    source_quality: str
    sensitivity: str
    origin: dict[str, str]
    risk_flags: tuple[str, ...] = ()
    safety: Optional[SafetyScanResult] = None

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
                self.relative_extract_path.as_posix()
                if self.relative_extract_path is not None
                else None
            ),
            "url": self.url,
            "title": self.title,
            "tags": list(self.tags),
            "channel": self.channel,
            "source_quality": self.source_quality,
            "sensitivity": self.sensitivity,
            "origin": dict(self.origin),
            "risk_flags": list(self.risk_flags),
            "safety": (self.safety or SafetyScanResult(self.risk_flags, ())).to_dict(),
            "citations": self.citations,
        }


@dataclass(frozen=True)
class PromotedMemoryResult:
    """One memory promoted from a saved source extract."""

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
            1 for memory in self.memories if memory.result.status == LifecycleStatus.PENDING
        )
        next_steps = ["Review the saved source and extract under Sources/."]
        if pending_count:
            next_steps.append("Review the pending atomic memories before approving them.")
        else:
            next_steps.append("Atomic memories were activated by the configured agent policy.")
        return {
            "ok": True,
            "implemented": True,
            "source": self.source.to_dict(),
            "memory_count": len(self.memories),
            "pending_count": pending_count,
            "review_required": pending_count > 0,
            "memories": [memory.to_dict() for memory in self.memories],
            "citations": self.citations,
            "next_steps": next_steps,
        }


@dataclass(frozen=True)
class SourceLookupChunk:
    """Compact source evidence returned by a lookup request."""

    path: Path
    relative_path: Path
    text: str
    tokens_estimate: int
    kind: str
    source_id: str

    @property
    def citation(self) -> dict[str, str]:
        return {
            "id": self.source_id,
            "path": self.relative_path.as_posix(),
            "kind": self.kind,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path.as_posix(),
            "text": self.text,
            "tokens_estimate": self.tokens_estimate,
            "citation": self.citation,
        }


@dataclass(frozen=True)
class _PlannedMemory:
    memory_type: MemoryType
    text: str
    scope: Optional[MemoryScope]
    project: Optional[str]
    tags: tuple[str, ...]
    confidence: float
    status: LifecycleStatus
    risk_flags: tuple[str, ...] = ()


_PROMOTABLE_MEMORY_TYPES = {
    MemoryType.FACT,
    MemoryType.PREFERENCE,
    MemoryType.DECISION,
    MemoryType.TASK,
    MemoryType.PROJECT_CONTEXT,
}


def lookup_source(
    config: MemoryConfig,
    source_id: str,
    query: Optional[str] = None,
    budget: int = 800,
    *,
    session_id: Any = None,
    loaded_source_ids: Any = None,
) -> dict[str, Any]:
    """Return compact, read-only source evidence for an exact Sources/<id> directory."""

    try:
        selected_budget = _lookup_budget(budget)
    except Exception as exc:
        return {
            "ok": False,
            "implemented": True,
            "tool": "lookup_source",
            "command": "lookup_source",
            "source_id": str(source_id).strip(),
            "query": _optional_string(query),
            "budget": budget,
            "chunks": [],
            "citations": [],
            "error": {
                "code": "invalid_budget",
                "message": str(exc),
            },
        }
    selected_source_id = str(source_id).strip()
    session_state = normalize_session_recall_state(
        session_id=session_id,
        loaded_source_ids=loaded_source_ids,
    )
    base_payload: dict[str, Any] = {
        "ok": True,
        "implemented": True,
        "tool": "lookup_source",
        "command": "lookup_source",
        "source_id": selected_source_id,
        "query": _optional_string(query),
        "budget": selected_budget,
        "chunks": [],
        "citations": [],
    }
    if session_state.requested:
        base_payload["session"] = session_trace(session_state)
    if (
        not selected_source_id
        or selected_source_id in {".", ".."}
        or Path(selected_source_id).name != selected_source_id
    ):
        return _lookup_error_payload(
            base_payload,
            code="source_not_found",
            message=f"source not found: {selected_source_id}",
        )

    if selected_source_id in session_state.loaded_source_id_set:
        base_payload.update(
            {
                "source_path": None,
                "fallback": False,
                "empty_reason": "session_filtered",
                "session": session_trace(
                    session_state,
                    filtered_source_ids=(selected_source_id,),
                ),
            }
        )
        return base_payload

    source_dir = config.vault_path / config.sources_dir / selected_source_id
    if not source_dir.is_dir():
        return _lookup_error_payload(
            base_payload,
            code="source_not_found",
            message=f"source not found: {selected_source_id}",
        )

    evidence_path = source_dir / "extract.md"
    kind = "source_extract"
    if not evidence_path.is_file():
        evidence_path = source_dir / "source.md"
        kind = "source"
    if not evidence_path.is_file():
        return _lookup_error_payload(
            base_payload,
            code="source_not_found",
            message=f"source has no extract.md or source.md: {selected_source_id}",
        )

    relative_path = evidence_path.relative_to(config.vault_path)
    raw_text = evidence_path.read_text(encoding="utf-8")
    text = _strip_frontmatter(raw_text)
    safety = merge_scan_results(
        scan_metadata(_frontmatter_mapping(raw_text)),
        scan_text(text, field=kind),
    )
    base_payload.update(
        {
            "risk_flags": list(safety.risk_flags),
            "safety": safety.to_dict(),
        }
    )
    if safety.blocks_default_recall:
        base_payload.update(
            {
                "source_path": relative_path.as_posix(),
                "fallback": False,
                "empty_reason": "safety_filtered",
                "safety_filtered": True,
            }
        )
        return base_payload
    raw_chunks = _source_text_chunks(text)
    query_tokens = _lookup_tokens(query)
    ranked_chunks, matched = _rank_source_chunks(raw_chunks, query_tokens)
    packed_chunks = _pack_source_chunks(
        ranked_chunks,
        budget=selected_budget,
        evidence_path=evidence_path,
        relative_path=relative_path,
        kind=kind,
        source_id=selected_source_id,
    )
    citations = _unique_citations(chunk.citation for chunk in packed_chunks)
    base_payload.update(
        {
            "chunks": [chunk.to_dict() for chunk in packed_chunks],
            "citations": citations,
            "source_path": relative_path.as_posix(),
            "fallback": bool(query_tokens and not matched),
            "empty_reason": _source_lookup_empty_reason(
                raw_chunks, packed_chunks, query_tokens, matched
            ),
        }
    )
    return base_payload


def save_source_material(
    config: MemoryConfig,
    *,
    title: Optional[str] = None,
    url: Optional[str] = None,
    content: Optional[str] = None,
    extract: Optional[str] = None,
    tags: Iterable[str] = (),
    channel: Optional[str] = None,
    source_quality: Optional[str] = None,
    sensitivity: Optional[str] = None,
    origin: Optional[Mapping[str, Any]] = None,
    slug: Optional[str] = None,
    captured_at: Optional[datetime] = None,
) -> SourceCaptureResult:
    """Save raw material under Sources without promoting it to canonical memory."""

    selected_at = captured_at or datetime.now(timezone.utc).astimezone()
    selected_title = _clean_title(title) or _title_from_url(url) or "Untitled source"
    selected_tags = tuple(_clean_list(tags))
    selected_channel = _normalized_choice(
        channel, SOURCE_CHANNELS, default="url" if _optional_string(url) else "manual"
    )
    selected_quality = _normalized_choice(source_quality, SOURCE_QUALITIES, default="user_provided")
    selected_sensitivity = _normalized_choice(sensitivity, SOURCE_SENSITIVITIES, default="normal")
    selected_origin = _clean_mapping(origin)
    safety = scan_source_material(
        content=_optional_string(content),
        extract=_optional_string(extract),
        metadata={
            "channel": selected_channel,
            "source_quality": selected_quality,
            "sensitivity": selected_sensitivity,
            **selected_origin,
        },
    )
    selected_slug = _slugify(slug or selected_title or url or "source")
    source_id = f"{selected_at:%Y-%m-%d}_{selected_slug}"
    sources_root = config.vault_path / config.sources_dir
    source_dir = _unique_source_dir(sources_root, source_id)
    source_id = source_dir.name
    has_extract = _optional_string(extract) is not None

    source_markdown = _render_source_markdown(
        source_id=source_id,
        title=selected_title,
        url=_optional_string(url),
        content=_optional_string(content),
        tags=selected_tags,
        channel=selected_channel,
        source_quality=selected_quality,
        sensitivity=selected_sensitivity,
        origin=selected_origin,
        safety=safety,
        captured_at=selected_at,
    )
    files: list[tuple[PathLike, str]] = [(source_dir / "source.md", source_markdown)]

    extract_path: Optional[Path] = None
    if has_extract:
        extract_path = source_dir / "extract.md"
        files.append(
            (
                extract_path,
                _render_extract_markdown(
                    source_id=source_id,
                    title=selected_title,
                    url=_optional_string(url),
                    extract=str(extract).strip(),
                    tags=selected_tags,
                    channel=selected_channel,
                    source_quality=selected_quality,
                    sensitivity=selected_sensitivity,
                    origin=selected_origin,
                    safety=safety,
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
        tags=selected_tags,
        channel=selected_channel,
        source_quality=selected_quality,
        sensitivity=selected_sensitivity,
        origin=selected_origin,
        risk_flags=safety.risk_flags,
        safety=safety,
    )


def save_source_with_memories(
    config: MemoryConfig,
    *,
    source: Mapping[str, Any],
    memories: Iterable[Mapping[str, Any]],
    author_name: str = "CLI agent",
) -> SourcePromotionResult:
    """Save source material and promote agent-supplied atomic memories for review."""

    source_payload = dict(source)
    selected_sensitivity = _normalized_choice(
        _optional_string(source_payload.get("sensitivity")),
        SOURCE_SENSITIVITIES,
        default="normal",
    )
    source_safety = scan_source_material(
        content=_optional_string(
            source_payload.get("content")
            or source_payload.get("raw")
            or source_payload.get("markdown")
        ),
        extract=_optional_string(source_payload.get("extract") or source_payload.get("summary")),
        metadata={
            "source_quality": _optional_string(source_payload.get("source_quality")) or "unknown",
            "sensitivity": selected_sensitivity,
        },
    )
    if selected_sensitivity in PROMOTION_BLOCKED_SENSITIVITIES:
        raise ValueError(
            "source sensitivity is blocked from memory promotion; "
            "save the source only and review it manually"
        )
    planned = tuple(
        _plan_promoted_memory(
            memory,
            default_project=_optional_string(source_payload.get("default_project")),
            policy=config.agent_policy,
        )
        for memory in memories
    )
    if not planned:
        raise ValueError("memories must include at least one durable atomic item")
    if selected_sensitivity == "private":
        planned = tuple(replace(item, status=LifecycleStatus.PENDING) for item in planned)
    if source_safety.risk_flags:
        planned = tuple(
            replace(
                item,
                status=LifecycleStatus.PENDING,
                risk_flags=normalize_risk_flags((*item.risk_flags, *source_safety.risk_flags)),
            )
            for item in planned
        )

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
        tags=_clean_list(source_payload.get("tags", ())),
        channel=_optional_string(source_payload.get("channel")),
        source_quality=_optional_string(source_payload.get("source_quality")),
        sensitivity=selected_sensitivity,
        origin=_mapping_or_none(source_payload.get("origin")),
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
            status=item.status,
            tags=item.tags,
            author_kind=AuthorKind.AGENT,
            author_name=author_name,
            source=source_ref,
            confidence=item.confidence,
            risk_flags=item.risk_flags,
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


def _lookup_budget(value: int) -> int:
    budget = int(value)
    if budget < 1:
        raise ValueError("budget must be at least 1")
    return budget


def _lookup_error_payload(payload: Mapping[str, Any], *, code: str, message: str) -> dict[str, Any]:
    result = dict(payload)
    result.update(
        {
            "ok": False,
            "chunks": [],
            "citations": [],
            "error": {
                "code": code,
                "message": message,
            },
        }
    )
    return result


def _strip_frontmatter(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if normalized.startswith("---\n"):
        parts = normalized.split("\n---\n", 1)
        if len(parts) == 2:
            normalized = parts[1]
    lines = normalized.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    if lines and not lines[0].strip():
        lines = lines[1:]
    if lines and lines[0].startswith("Source URL: "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _frontmatter_mapping(text: str) -> Mapping[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    payload = yaml.safe_load(parts[0][4:]) or {}
    return payload if isinstance(payload, Mapping) else {}


def _source_text_chunks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if len(lines) > 1:
            chunks.extend(lines)
        else:
            chunks.append(paragraph)
    return chunks


def _lookup_tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _rank_source_chunks(chunks: list[str], query_tokens: set[str]) -> tuple[list[str], bool]:
    if not query_tokens:
        return chunks, False
    scored: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        overlap = len(query_tokens & _lookup_tokens(chunk))
        scored.append((overlap, index, chunk))
    matches = [item for item in scored if item[0] > 0]
    if not matches:
        return chunks, False
    matches.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for _, _, chunk in matches], True


def _pack_source_chunks(
    chunks: list[str],
    *,
    budget: int,
    evidence_path: Path,
    relative_path: Path,
    kind: str,
    source_id: str,
) -> list[SourceLookupChunk]:
    remaining = budget
    packed: list[SourceLookupChunk] = []
    for chunk in chunks:
        if remaining < 1:
            break
        text = _trim_chunk_to_budget(chunk, remaining)
        if not text:
            break
        token_estimate = estimate_tokens(text)
        if token_estimate > remaining:
            break
        packed.append(
            SourceLookupChunk(
                path=evidence_path,
                relative_path=relative_path,
                text=text,
                tokens_estimate=token_estimate,
                kind=kind,
                source_id=source_id,
            )
        )
        remaining -= token_estimate
    return packed


def _trim_chunk_to_budget(text: str, budget: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned or estimate_tokens(cleaned) <= budget:
        return cleaned
    words = cleaned.split()
    max_words = max(1, int(budget * 0.75))
    candidate = " ".join(words[:max_words]).strip()
    while candidate and estimate_tokens(candidate) > budget:
        words = candidate.split()[:-1]
        candidate = " ".join(words).strip()
    return candidate


def _unique_citations(citations: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for citation in citations:
        key = (citation["id"], citation["path"], citation["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(citation))
    return unique


def _source_lookup_empty_reason(
    raw_chunks: list[str],
    packed_chunks: list[SourceLookupChunk],
    query_tokens: set[str],
    matched: bool,
) -> Optional[str]:
    if not raw_chunks:
        return "source_empty"
    if query_tokens and not matched:
        return "no_query_overlap"
    if not packed_chunks:
        return "budget_exhausted"
    return None


def _render_source_markdown(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    content: Optional[str],
    tags: tuple[str, ...],
    channel: str,
    source_quality: str,
    sensitivity: str,
    origin: Mapping[str, str],
    safety: SafetyScanResult,
    captured_at: datetime,
) -> str:
    frontmatter = _frontmatter(
        source_id=source_id,
        title=title,
        url=url,
        tags=tags,
        channel=channel,
        source_quality=source_quality,
        sensitivity=sensitivity,
        origin=origin,
        safety=safety,
        captured_at=captured_at,
        kind="source",
    )
    body = content or (
        "No raw content was provided to Memora. The agent should fetch or "
        "read the URL externally, then call save_source again with content and an extract."
    )
    return f"---\n{frontmatter}\n---\n\n# {title}\n\n{_source_url_line(url)}{body.strip()}\n"


def _render_extract_markdown(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    extract: str,
    tags: tuple[str, ...],
    channel: str,
    source_quality: str,
    sensitivity: str,
    origin: Mapping[str, str],
    safety: SafetyScanResult,
    captured_at: datetime,
) -> str:
    frontmatter = _frontmatter(
        source_id=source_id,
        title=title,
        url=url,
        tags=tags,
        channel=channel,
        source_quality=source_quality,
        sensitivity=sensitivity,
        origin=origin,
        safety=safety,
        captured_at=captured_at,
        kind="extract",
    )
    return f"---\n{frontmatter}\n---\n\n# Extract: {title}\n\n{_source_url_line(url)}{extract.strip()}\n"


def _frontmatter(
    *,
    source_id: str,
    title: str,
    url: Optional[str],
    tags: tuple[str, ...],
    channel: str,
    source_quality: str,
    sensitivity: str,
    origin: Mapping[str, str],
    safety: SafetyScanResult,
    captured_at: datetime,
    kind: str,
) -> str:
    data = {
        "source_id": source_id,
        "kind": kind,
        "schema_version": 1,
        "title": title,
        "url": url,
        "tags": list(tags),
        "captured_at": captured_at.isoformat(),
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "risk_flags": list(safety.risk_flags),
    }
    if origin:
        data["origin"] = dict(origin)
    for field in ("url", "tags", "risk_flags"):
        if data.get(field) in (None, []):
            data.pop(field, None)
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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _mapping_or_none(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _clean_mapping(value: Optional[Mapping[str, Any]]) -> dict[str, str]:
    if not value:
        return {}
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        cleaned_key = _optional_string(str(key))
        cleaned_value = _optional_string(item)
        if cleaned_key and cleaned_value:
            cleaned[cleaned_key] = cleaned_value
    return cleaned


def _normalized_choice(value: Optional[str], allowed: set[str], *, default: str) -> str:
    selected = (_optional_string(value) or default).strip().lower()
    if selected not in allowed and not (
        allowed is SOURCE_CHANNELS and _SCHEDULED_CHANNEL_RE.fullmatch(selected)
    ):
        raise ValueError(f"value must be one of: {', '.join(sorted(allowed))}")
    return selected


def _plan_promoted_memory(
    memory: Mapping[str, Any],
    *,
    default_project: Optional[str],
    policy: AgentPolicyConfig,
) -> _PlannedMemory:
    memory_type = MemoryType(memory.get("type", MemoryType.FACT.value))
    if memory_type not in _PROMOTABLE_MEMORY_TYPES:
        allowed = ", ".join(sorted(memory_type.value for memory_type in _PROMOTABLE_MEMORY_TYPES))
        raise ValueError(f"source promotion only supports durable atomic memory types: {allowed}")

    confidence = float(memory.get("confidence", policy.min_pending_confidence))
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")

    text = _memory_text(memory)
    safety = scan_text(text, field="memory")
    risk_flags = normalize_risk_flags(
        (*_clean_list(memory.get("risk_flags", ())), *safety.risk_flags)
    )
    selected_status = _promoted_memory_status(memory, policy, confidence)
    if risk_flags and selected_status == LifecycleStatus.ACTIVE:
        selected_status = LifecycleStatus.PENDING

    return _PlannedMemory(
        memory_type=memory_type,
        text=text,
        scope=_optional_enum(MemoryScope, memory.get("scope")),
        project=_optional_string(memory.get("project")) or default_project,
        tags=tuple(_clean_list(memory.get("tags", ()))),
        confidence=confidence,
        status=selected_status,
        risk_flags=risk_flags,
    )


def _promoted_memory_status(
    memory: Mapping[str, Any],
    policy: AgentPolicyConfig,
    confidence: float,
) -> LifecycleStatus:
    if policy.require_review_for_source_promotions:
        return LifecycleStatus.PENDING

    trust_level = _trust_level(policy)
    explicit_user_save = _explicit_user_save(memory)
    requested = _optional_string(memory.get("status"))
    requested_status = LifecycleStatus(requested) if requested else None
    if requested_status and requested_status != LifecycleStatus.ACTIVE:
        return (
            requested_status
            if trust_level == AgentTrustLevel.AUTONOMOUS.value
            else LifecycleStatus.PENDING
        )

    if confidence < policy.min_active_confidence:
        return LifecycleStatus.PENDING
    if trust_level == AgentTrustLevel.AUTONOMOUS.value:
        return LifecycleStatus.ACTIVE
    if (
        trust_level == AgentTrustLevel.EXPLICIT_ACTIVE.value
        and policy.explicit_user_saves_active
        and explicit_user_save
    ):
        return LifecycleStatus.ACTIVE
    return LifecycleStatus.PENDING


def _explicit_user_save(memory: Mapping[str, Any]) -> bool:
    return any(
        _bool(memory.get(key))
        for key in (
            "explicit_user_save",
            "user_explicit",
            "authorized_by_user",
            "direct_user_instruction",
        )
    )


def _trust_level(policy: AgentPolicyConfig) -> str:
    value = policy.trust_level
    return value.value if isinstance(value, AgentTrustLevel) else str(value)


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
    "SourceLookupChunk",
    "SourcePromotionResult",
    "lookup_source",
    "save_source_material",
    "save_source_with_memories",
]
