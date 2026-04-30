"""Lifecycle mutation helpers for canonical Markdown memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

import yaml

from agent_memory.config import MemoryConfig
from agent_memory.schema import (
    AuthorKind,
    LifecycleStatus,
    MemoryDocument,
    MemoryFrontmatter,
    RelationType,
    iter_memory_markdown_files,
    parse_markdown_document,
)
from agent_memory.sync import atomic_write_many, atomic_write_text, vault_lock

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<yaml>.*?)(?:\n---[ \t]*)(?:\n(?P<body>.*))?\Z",
    re.DOTALL,
)
_TERMINAL_STATUSES = {
    LifecycleStatus.STALE.value,
    LifecycleStatus.SUPERSEDED.value,
    LifecycleStatus.REJECTED.value,
}


@dataclass(frozen=True)
class LifecycleMutation:
    """One durable memory file mutation."""

    memory_id: str
    path: Path
    relative_path: Path
    previous_status: str
    status: str
    action: str

    @property
    def citation(self) -> dict[str, str]:
        return {
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "kind": "memory",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "previous_status": self.previous_status,
            "status": self.status,
            "action": self.action,
            "citation": self.citation,
        }


@dataclass(frozen=True)
class LifecycleResult:
    """Structured result for a lifecycle command."""

    command: str
    mutations: tuple[LifecycleMutation, ...]
    details: dict[str, Any]

    @property
    def citations(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        citations: list[dict[str, str]] = []
        for mutation in self.mutations:
            if mutation.memory_id in seen:
                continue
            seen.add(mutation.memory_id)
            citations.append(mutation.citation)
        return citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "command": self.command,
            "mutation_count": len(self.mutations),
            "mutations": [mutation.to_dict() for mutation in self.mutations],
            "citations": self.citations,
            **self.details,
        }


@dataclass(frozen=True)
class ReviewItem:
    """One pending memory that needs human review."""

    memory_id: str
    path: Path
    relative_path: Path
    memory_type: str
    status: str
    confidence: Optional[float]
    author: dict[str, Any]
    source: Optional[dict[str, Any]]
    body: str
    updated_at: str

    @property
    def citation(self) -> dict[str, str]:
        return {
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "kind": "memory",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "type": self.memory_type,
            "status": self.status,
            "confidence": self.confidence,
            "author": self.author,
            "source": self.source,
            "body": self.body.strip(),
            "updated_at": self.updated_at,
            "citation": self.citation,
        }


@dataclass(frozen=True)
class ReviewQueue:
    """Pending agent-generated memories awaiting review."""

    config: MemoryConfig
    items: tuple[ReviewItem, ...]

    @property
    def citations(self) -> list[dict[str, str]]:
        return [item.citation for item in self.items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "command": "review",
            "vault_path": str(self.config.vault_path),
            "pending_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
            "citations": self.citations,
        }


def mark_status(
    config: MemoryConfig,
    memory_id: str,
    status: Union[LifecycleStatus, str],
    *,
    reason: Optional[str] = None,
    _action: str = "mark_status",
) -> LifecycleResult:
    """Set a memory lifecycle status and append an audit history entry."""

    selected_status = _status_value(status)
    with vault_lock(config):
        return _mark_status_unlocked(
            config,
            memory_id,
            selected_status,
            reason=reason,
            _action=_action,
        )


def _mark_status_unlocked(
    config: MemoryConfig,
    memory_id: str,
    selected_status: str,
    *,
    reason: Optional[str],
    _action: str,
) -> LifecycleResult:
    record = _find_memory(config, memory_id)
    previous_status = str(record.data.get("status"))
    if previous_status == selected_status:
        return LifecycleResult(
            command="mark",
            mutations=(),
            details={
                "id": memory_id,
                "status": selected_status,
                "changed": False,
                "message": "memory already has requested status",
            },
        )

    now = _now()
    today = now.date().isoformat()
    record.data["status"] = selected_status
    record.data["updated_at"] = now.isoformat()
    if selected_status in _TERMINAL_STATUSES:
        record.data["valid_to"] = record.data.get("valid_to") or _valid_to_for(record.data, today)
    elif selected_status in {LifecycleStatus.ACTIVE.value, LifecycleStatus.PENDING.value}:
        record.data["valid_to"] = None
    _append_history(
        record.data,
        action=_action,
        at=now,
        previous_status=previous_status,
        status=selected_status,
        reason=reason,
    )

    rendered = _render_updated(record.data, record.body, path=record.path)
    _atomic_replace(record.path, rendered)
    mutation = _mutation(config, record, previous_status, selected_status, _action)
    return LifecycleResult(
        command="mark",
        mutations=(mutation,),
        details={"id": memory_id, "status": selected_status, "changed": True},
    )


def reject_memory(
    config: MemoryConfig,
    memory_id: str,
    *,
    reason: Optional[str] = None,
) -> LifecycleResult:
    """Reject a memory so it is excluded by default retrieval."""

    result = mark_status(config, memory_id, LifecycleStatus.REJECTED, reason=reason, _action="reject")
    return LifecycleResult(command="reject", mutations=result.mutations, details=result.details)


def supersede_memory(
    config: MemoryConfig,
    old_id: str,
    *,
    new_id: str,
    reason: Optional[str] = None,
) -> LifecycleResult:
    """Mark an old memory superseded and link it from the replacement memory."""

    with vault_lock(config):
        return _supersede_memory_unlocked(config, old_id, new_id=new_id, reason=reason)


def _supersede_memory_unlocked(
    config: MemoryConfig,
    old_id: str,
    *,
    new_id: str,
    reason: Optional[str],
) -> LifecycleResult:
    if old_id == new_id:
        raise ValueError("old_id and new_id must be different")

    old_record = _find_memory(config, old_id)
    new_record = _find_memory(config, new_id)
    now = _now()
    today = now.date().isoformat()

    old_previous = str(old_record.data.get("status"))
    new_previous = str(new_record.data.get("status"))

    old_record.data["status"] = LifecycleStatus.SUPERSEDED.value
    old_record.data["valid_to"] = old_record.data.get("valid_to") or _valid_to_for(old_record.data, today)
    old_record.data["updated_at"] = now.isoformat()
    _append_history(
        old_record.data,
        action="superseded",
        at=now,
        previous_status=old_previous,
        status=LifecycleStatus.SUPERSEDED.value,
        by=new_id,
        reason=reason,
    )

    supersedes = _string_list(new_record.data.get("supersedes"))
    if old_id not in supersedes:
        supersedes.append(old_id)
    new_record.data["supersedes"] = supersedes
    new_record.data["updated_at"] = now.isoformat()
    _append_history(
        new_record.data,
        action="supersedes",
        at=now,
        previous_status=new_previous,
        status=str(new_record.data.get("status")),
        target=old_id,
        reason=reason,
    )

    rendered_old = _render_updated(old_record.data, old_record.body, path=old_record.path)
    rendered_new = _render_updated(new_record.data, new_record.body, path=new_record.path)
    _atomic_replace_many(((old_record.path, rendered_old), (new_record.path, rendered_new)))

    return LifecycleResult(
        command="supersede",
        mutations=(
            _mutation(
                config,
                old_record,
                old_previous,
                LifecycleStatus.SUPERSEDED.value,
                "superseded",
            ),
            _mutation(config, new_record, new_previous, str(new_record.data.get("status")), "supersedes"),
        ),
        details={"old_id": old_id, "new_id": new_id, "relation": RelationType.SUPERSEDES.value},
    )


def contradict_memories(
    config: MemoryConfig,
    id1: str,
    id2: str,
    *,
    reason: Optional[str] = None,
) -> LifecycleResult:
    """Record a contradiction edge from the first memory to the second."""

    with vault_lock(config):
        return _contradict_memories_unlocked(config, id1, id2, reason=reason)


def _contradict_memories_unlocked(
    config: MemoryConfig,
    id1: str,
    id2: str,
    *,
    reason: Optional[str],
) -> LifecycleResult:
    if id1 == id2:
        raise ValueError("contradicting memory ids must be different")

    left = _find_memory(config, id1)
    right = _find_memory(config, id2)
    now = _now()
    left_previous = str(left.data.get("status"))
    right_previous = str(right.data.get("status"))

    contradicts = _string_list(left.data.get("contradicts"))
    if id2 not in contradicts:
        contradicts.append(id2)
    left.data["contradicts"] = contradicts
    left.data["updated_at"] = now.isoformat()
    _append_history(
        left.data,
        action="contradicts",
        at=now,
        previous_status=left_previous,
        status=str(left.data.get("status")),
        target=id2,
        reason=reason,
    )

    _append_history(
        right.data,
        action="contradicted_by",
        at=now,
        previous_status=right_previous,
        status=str(right.data.get("status")),
        by=id1,
        reason=reason,
    )

    rendered_left = _render_updated(left.data, left.body, path=left.path)
    rendered_right = _render_updated(right.data, right.body, path=right.path)
    _atomic_replace_many(((left.path, rendered_left), (right.path, rendered_right)))

    return LifecycleResult(
        command="contradict",
        mutations=(
            _mutation(config, left, left_previous, str(left.data.get("status")), "contradicts"),
            _mutation(config, right, right_previous, str(right.data.get("status")), "contradicted_by"),
        ),
        details={"id1": id1, "id2": id2, "relation": RelationType.CONTRADICTS.value},
    )


def decay_memories(config: MemoryConfig, *, now: Optional[datetime] = None) -> LifecycleResult:
    """Mark active memories with expired valid_to dates as stale."""

    with vault_lock(config):
        return _decay_memories_unlocked(config, now=now)


def _decay_memories_unlocked(config: MemoryConfig, *, now: Optional[datetime] = None) -> LifecycleResult:
    selected_now = now or _now()
    today = selected_now.date()
    updates: list[tuple[_MemoryRecord, str, str]] = []
    rendered: list[tuple[Path, str]] = []

    for record in _iter_memory_records(config):
        previous_status = str(record.data.get("status"))
        valid_to = _optional_date_string(record.data.get("valid_to"))
        if previous_status != LifecycleStatus.ACTIVE.value or valid_to is None or valid_to >= today.isoformat():
            continue
        record.data["status"] = LifecycleStatus.STALE.value
        record.data["updated_at"] = selected_now.isoformat()
        _append_history(
            record.data,
            action="decay",
            at=selected_now,
            previous_status=previous_status,
            status=LifecycleStatus.STALE.value,
            reason="valid_to elapsed",
        )
        rendered.append((record.path, _render_updated(record.data, record.body, path=record.path)))
        updates.append((record, previous_status, LifecycleStatus.STALE.value))

    _atomic_replace_many(rendered)
    mutations = tuple(_mutation(config, record, previous, status, "decay") for record, previous, status in updates)
    return LifecycleResult(
        command="decay",
        mutations=mutations,
        details={"changed": len(mutations), "as_of": today.isoformat()},
    )


def review_queue(config: MemoryConfig) -> ReviewQueue:
    """Return pending agent-generated memories for explicit review."""

    items: list[ReviewItem] = []
    for record in _iter_memory_records(config):
        frontmatter = record.document.frontmatter
        author = frontmatter.author
        if frontmatter.status != LifecycleStatus.PENDING:
            continue
        if author is None or author.kind != AuthorKind.AGENT:
            continue
        items.append(
            ReviewItem(
                memory_id=frontmatter.id,
                path=record.path,
                relative_path=record.path.relative_to(config.vault_path),
                memory_type=frontmatter.type.value,
                status=frontmatter.status.value,
                confidence=frontmatter.confidence,
                author=author.model_dump(mode="json"),
                source=frontmatter.source.model_dump(mode="json") if frontmatter.source else None,
                body=record.body,
                updated_at=frontmatter.updated_at.isoformat(),
            )
        )
    items.sort(key=lambda item: (item.updated_at, item.relative_path.as_posix()))
    return ReviewQueue(config=config, items=tuple(items))


def touch_last_used(config: MemoryConfig, memory_ids: Iterable[str], *, when: Optional[datetime] = None) -> None:
    """Best-effort frontmatter touch after recall without changing memory status."""

    with vault_lock(config):
        _touch_last_used_unlocked(config, memory_ids, when=when)


def _touch_last_used_unlocked(
    config: MemoryConfig,
    memory_ids: Iterable[str],
    *,
    when: Optional[datetime] = None,
) -> None:
    selected_when = when or _now()
    targets = set(memory_ids)
    if not targets:
        return
    rendered: list[tuple[Path, str]] = []
    for record in _iter_memory_records(config):
        if record.document.frontmatter.id not in targets:
            continue
        record.data["last_used_at"] = selected_when.isoformat()
        rendered.append((record.path, _render_updated(record.data, record.body, path=record.path)))
    _atomic_replace_many(rendered)


@dataclass
class _MemoryRecord:
    path: Path
    document: MemoryDocument
    data: dict[str, Any]
    body: str


def _find_memory(config: MemoryConfig, memory_id: str) -> _MemoryRecord:
    for record in _iter_memory_records(config):
        if record.document.frontmatter.id == memory_id:
            return record
    raise ValueError(f"memory not found: {memory_id}")


def _iter_memory_records(config: MemoryConfig) -> tuple[_MemoryRecord, ...]:
    records: list[_MemoryRecord] = []
    for path in iter_memory_markdown_files(config.vault_path):
        records.append(_read_record(path))
    return tuple(records)


def _read_record(path: Path) -> _MemoryRecord:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise ValueError(f"{path}: memory Markdown must start with YAML frontmatter delimited by ---")
    document = parse_markdown_document(raw, path=path)
    data = document.frontmatter.model_dump(mode="json", exclude_none=False)
    return _MemoryRecord(path=path, document=document, data=data, body=match.group("body") or "")


def _render_updated(data: dict[str, Any], body: str, *, path: Path) -> str:
    frontmatter = MemoryFrontmatter.model_validate(data)
    frontmatter_data = frontmatter.model_dump(mode="json", exclude_none=False)
    rendered_yaml = yaml.safe_dump(frontmatter_data, sort_keys=False, allow_unicode=False).strip()
    markdown = f"---\n{rendered_yaml}\n---\n{body}"
    parse_markdown_document(markdown, path=path)
    return markdown


def _atomic_replace(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def _atomic_replace_many(files: Sequence[tuple[Path, str]]) -> None:
    atomic_write_many(files)


def _append_history(
    data: dict[str, Any],
    *,
    action: str,
    at: datetime,
    previous_status: str,
    status: str,
    reason: Optional[str] = None,
    **details: Any,
) -> None:
    history = data.get("history")
    if not isinstance(history, list):
        history = []
    entry = {
        "at": at.isoformat(),
        "action": action,
        "actor": "agent-memory",
        "from_status": previous_status,
        "to_status": status,
    }
    for key, value in details.items():
        if value is not None:
            entry[key] = value
    if reason:
        entry["reason"] = reason
    history.append(entry)
    data["history"] = history


def _mutation(
    config: MemoryConfig,
    record: _MemoryRecord,
    previous_status: str,
    status: str,
    action: str,
) -> LifecycleMutation:
    return LifecycleMutation(
        memory_id=record.document.frontmatter.id,
        path=record.path,
        relative_path=record.path.relative_to(config.vault_path),
        previous_status=previous_status,
        status=status,
        action=action,
    )


def _status_value(status: Union[LifecycleStatus, str]) -> str:
    return LifecycleStatus(status).value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _optional_date_string(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _valid_to_for(data: dict[str, Any], today: str) -> str:
    valid_from = _optional_date_string(data.get("valid_from"))
    if valid_from and valid_from > today:
        return valid_from
    return today


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


__all__ = [
    "LifecycleMutation",
    "LifecycleResult",
    "ReviewItem",
    "ReviewQueue",
    "contradict_memories",
    "decay_memories",
    "mark_status",
    "reject_memory",
    "review_queue",
    "supersede_memory",
    "touch_last_used",
]
