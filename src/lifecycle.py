"""Lifecycle mutation helpers for canonical Markdown memories."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from config import MemoryConfig
from curation import detect_opposite_claim, near_duplicate_text
from schema import (
    AuthorKind,
    LifecycleStatus,
    MemoryDocument,
    MemoryFrontmatter,
    MemoryScope,
    MemoryType,
    RelationType,
    iter_memory_markdown_files,
    parse_markdown_document,
)
from safety import (
    has_unsafe_recall_risk,
    merge_scan_results,
    normalize_risk_flags,
    scan_metadata,
    scan_text,
)
from sync import atomic_write_text, vault_lock
from vault import MEMORY_TYPE_DIRECTORIES

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<yaml>.*?)(?:\n---[ \t]*)(?:\n(?P<body>.*))?\Z",
    re.DOTALL,
)
_TERMINAL_STATUSES = {
    LifecycleStatus.STALE.value,
    LifecycleStatus.SUPERSEDED.value,
    LifecycleStatus.REJECTED.value,
}
_DUPLICATE_CANDIDATE_STATUSES = {
    LifecycleStatus.ACTIVE.value,
    LifecycleStatus.PENDING.value,
}
_HIGH_IMPORTANCE_THRESHOLD = 0.8
_MISSING = object()


@dataclass(frozen=True)
class ReviewImportance:
    """Review-only ranking metadata for a pending memory."""

    score: float
    source: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "source": self.source,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class DuplicateCandidate:
    """Existing memory with exact normalized content matching a review item."""

    memory_id: str
    relative_path: Path
    memory_type: str
    status: str
    match_reason: str
    signature: str
    matched_fields: dict[str, tuple[str, ...]]
    score: Optional[float] = None
    confidence: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.memory_id,
            "relative_path": self.relative_path.as_posix(),
            "type": self.memory_type,
            "status": self.status,
            "match_reason": self.match_reason,
            "signature": self.signature,
            "matched_fields": {side: list(fields) for side, fields in self.matched_fields.items()},
        }
        if self.score is not None:
            payload["score"] = self.score
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload


@dataclass(frozen=True)
class ContradictionCandidate:
    """Memory explicitly connected to a review item by a contradiction signal."""

    memory_id: str
    relative_path: Optional[Path]
    memory_type: Optional[str]
    status: Optional[str]
    relation_direction: str
    match_reason: str
    confidence: Optional[float] = None
    matched_fields: Optional[dict[str, tuple[str, ...]]] = None
    evidence: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.memory_id,
            "relation_direction": self.relation_direction,
            "match_reason": self.match_reason,
        }
        if self.relative_path is not None:
            payload["relative_path"] = self.relative_path.as_posix()
        if self.memory_type is not None:
            payload["type"] = self.memory_type
        if self.status is not None:
            payload["status"] = self.status
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.matched_fields is not None:
            payload["matched_fields"] = {
                side: list(fields) for side, fields in self.matched_fields.items()
            }
        if self.evidence is not None:
            payload["evidence"] = dict(self.evidence)
        return payload


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
class MemoryUpdateOptions:
    """Explicit metadata/body fields requested by `memora memory update`."""

    memory_type: Optional[MemoryType] = None
    scope: Optional[MemoryScope] = None
    project: Optional[str] = None
    clear_project: bool = False
    status: Optional[LifecycleStatus] = None
    confidence: Optional[float] = None
    clear_confidence: bool = False
    tags: Optional[tuple[str, ...]] = None
    clear_tags: bool = False
    title: Optional[str] = None
    clear_title: bool = False
    text: Optional[str] = None


@dataclass(frozen=True)
class ReviewBatchItemResult:
    """Per-memory outcome for a batch review action."""

    memory_id: str
    action: str
    ok: bool
    dry_run: bool
    planned: bool = False
    previous_status: Optional[str] = None
    status: Optional[str] = None
    path: Optional[Path] = None
    relative_path: Optional[Path] = None
    risk_flags: tuple[str, ...] = ()
    mutation: Optional[LifecycleMutation] = None
    error: Optional[dict[str, str]] = None

    @property
    def citation(self) -> Optional[dict[str, str]]:
        if self.relative_path is None:
            return None
        return {
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "kind": "memory",
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.memory_id,
            "action": self.action,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "planned": self.planned,
            "previous_status": self.previous_status,
            "status": self.status,
            "risk_flags": list(self.risk_flags),
        }
        if self.path is not None:
            payload["path"] = str(self.path)
        if self.relative_path is not None:
            payload["relative_path"] = self.relative_path.as_posix()
        if self.mutation is not None:
            payload["mutation"] = self.mutation.to_dict()
        if self.error is not None:
            payload["error"] = dict(self.error)
        citation = self.citation
        if citation is not None:
            payload["citation"] = citation
        return payload


@dataclass(frozen=True)
class ReviewBatchResult:
    """Structured result for batch review actions over pending memories."""

    command: str
    action: str
    dry_run: bool
    results: tuple[ReviewBatchItemResult, ...]
    reason: Optional[str] = None
    details: Optional[dict[str, Any]] = None

    @property
    def mutations(self) -> tuple[LifecycleMutation, ...]:
        return tuple(result.mutation for result in self.results if result.mutation is not None)

    @property
    def citations(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        citations: list[dict[str, str]] = []
        for result in self.results:
            citation = result.citation
            if citation is None or result.memory_id in seen:
                continue
            seen.add(result.memory_id)
            citations.append(citation)
        return citations

    def to_dict(self) -> dict[str, Any]:
        failure_count = sum(1 for result in self.results if not result.ok)
        success_count = len(self.results) - failure_count
        return {
            "ok": failure_count == 0,
            "implemented": True,
            "command": self.command,
            "action": self.action,
            "dry_run": self.dry_run,
            "reason": self.reason,
            "result_count": len(self.results),
            "success_count": success_count,
            "failure_count": failure_count,
            "mutation_count": len(self.mutations),
            "results": [result.to_dict() for result in self.results],
            "mutations": [mutation.to_dict() for mutation in self.mutations],
            "citations": self.citations,
            **(self.details or {}),
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
    importance: ReviewImportance
    body: str
    updated_at: str
    risk_flags: tuple[str, ...] = ()
    recommended_action: str = "inspect"
    proposed_actions: tuple[str, ...] = ("approve", "reject", "inspect")
    duplicate_candidates: tuple[DuplicateCandidate, ...] = ()
    contradiction_candidates: tuple[ContradictionCandidate, ...] = ()

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
            "importance": self.importance.to_dict(),
            "body": self.body.strip(),
            "updated_at": self.updated_at,
            "risk_flags": list(self.risk_flags),
            "recommended_action": self.recommended_action,
            "proposed_actions": list(self.proposed_actions),
            "duplicate_candidates": [
                candidate.to_dict() for candidate in self.duplicate_candidates
            ],
            "contradiction_candidates": [
                candidate.to_dict() for candidate in self.contradiction_candidates
            ],
            "curation": _curation_metadata(
                self, recommended_action=_curation_recommended_action(self)
            ),
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
            "source_groups": _source_groups(self.items),
            "citations": self.citations,
        }


def _mark_status_unlocked(
    config: MemoryConfig,
    memory_id: str,
    selected_status: str,
    *,
    reason: Optional[str],
    _action: str,
    _record_noop: bool = False,
) -> LifecycleResult:
    record = _find_memory(config, memory_id)
    previous_status = str(record.data.get("status"))
    if previous_status == selected_status:
        if _record_noop:
            now = _now()
            record.data["updated_at"] = now.isoformat()
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


def update_memory(
    config: MemoryConfig,
    memory_id: str,
    options: MemoryUpdateOptions,
    *,
    reason: Optional[str] = None,
    dry_run: bool = False,
) -> LifecycleResult:
    """Update safe, user-editable memory fields through validated Markdown."""

    _validate_update_options(options)
    if dry_run:
        record = _find_memory(config, memory_id)
        return _update_memory_unlocked(config, record, options, reason=reason, dry_run=True)

    with vault_lock(config):
        record = _find_memory(config, memory_id)
        return _update_memory_unlocked(config, record, options, reason=reason, dry_run=False)


def _update_memory_unlocked(
    config: MemoryConfig,
    record: _MemoryRecord,
    options: MemoryUpdateOptions,
    *,
    reason: Optional[str],
    dry_run: bool,
) -> LifecycleResult:
    previous_status = str(record.data.get("status"))
    previous_path = record.path
    previous = _update_summary(record.data, record.body, record.path, config=config)
    data = dict(record.data)
    body = record.body
    changes: list[str] = []

    old_type = str(data.get("type"))
    old_confidence = data.get("confidence")
    old_body_text = body.strip()

    if options.memory_type is not None:
        selected_type = MemoryType(options.memory_type).value
        if selected_type == MemoryType.SOURCE_EXTRACT.value:
            raise ValueError(
                "source_extract memories are retired; use `memora source add` for evidence "
                "and `memora wiki synthesize --save` for durable source-backed summaries"
            )
        if data.get("type") != selected_type:
            data["type"] = selected_type
            _retag_observations(data, old_type=old_type, new_type=selected_type)
            changes.append("type")

    if options.scope is not None:
        selected_scope = MemoryScope(options.scope).value
        if data.get("scope") != selected_scope:
            data["scope"] = selected_scope
            changes.append("scope")

    if options.clear_project:
        if data.get("project") is not None:
            data["project"] = None
            changes.append("project")
    elif options.project is not None:
        selected_project = _clean_optional_text(options.project, field="project")
        if data.get("project") != selected_project:
            data["project"] = selected_project
            changes.append("project")
    elif data.get("scope") != MemoryScope.PROJECT.value and data.get("project") is not None:
        data["project"] = None
        changes.append("project")

    if options.status is not None:
        selected_status = LifecycleStatus(options.status).value
        if data.get("status") != selected_status:
            data["status"] = selected_status
            today = _now().date().isoformat()
            if selected_status in _TERMINAL_STATUSES:
                data["valid_to"] = data.get("valid_to") or _valid_to_for(data, today)
            elif selected_status in {LifecycleStatus.ACTIVE.value, LifecycleStatus.PENDING.value}:
                data["valid_to"] = None
            changes.append("status")

    if options.clear_confidence:
        if data.get("confidence") is not None:
            data["confidence"] = None
            _update_observation_confidence(data, old_confidence=old_confidence, new_confidence=None)
            changes.append("confidence")
    elif options.confidence is not None:
        selected_confidence = float(options.confidence)
        if data.get("confidence") != selected_confidence:
            data["confidence"] = selected_confidence
            _update_observation_confidence(
                data,
                old_confidence=old_confidence,
                new_confidence=selected_confidence,
            )
            changes.append("confidence")

    if options.clear_tags:
        if data.get("tags"):
            data["tags"] = []
            changes.append("tags")
    elif options.tags is not None:
        selected_tags = _normalize_tag_updates(options.tags)
        if data.get("tags") != selected_tags:
            data["tags"] = selected_tags
            changes.append("tags")

    if options.clear_title:
        if data.get("title") is not None:
            data["title"] = None
            data["aliases"] = []
            changes.append("title")
    elif options.title is not None:
        selected_title = _clean_optional_text(options.title, field="title")
        if data.get("title") != selected_title:
            data["title"] = selected_title
            changes.append("title")

    if options.text is not None:
        selected_text = options.text.strip()
        if not selected_text:
            raise ValueError("memory text must not be empty")
        if old_body_text != selected_text:
            body = f"\n\n{selected_text}\n"
            _update_observation_text(data, old_text=old_body_text, new_text=selected_text)
            changes.append("text")

    if {"text", "tags"} & set(changes):
        safety = merge_scan_results(
            scan_text(body.strip(), field="memory"),
            scan_text(" ".join(_string_list(data.get("tags"))), field="tags"),
        )
        merged_risk_flags = normalize_risk_flags(
            (*_string_list(data.get("risk_flags")), *safety.risk_flags)
        )
        data["risk_flags"] = merged_risk_flags

    if not changes:
        return LifecycleResult(
            command="memory update",
            mutations=(),
            details={
                "id": record.document.frontmatter.id,
                "changed": False,
                "dry_run": dry_run,
                "message": "memory already has requested values",
                "previous": previous,
                "updated": previous,
                "changes": [],
            },
        )

    now = _now()
    data["updated_at"] = now.isoformat()
    _append_history(
        data,
        action="update",
        at=now,
        previous_status=previous_status,
        status=str(data.get("status")),
        reason=reason,
        changes=changes,
    )

    target_path = _target_path_for_update(config, record.path, MemoryType(data["type"]))
    rendered = _render_updated(data, body, path=target_path)
    updated = _update_summary(data, body, target_path, config=config)

    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace(target_path, rendered)
        if target_path != record.path:
            record.path.unlink()

    mutation = LifecycleMutation(
        memory_id=record.document.frontmatter.id,
        path=target_path,
        relative_path=target_path.relative_to(config.vault_path),
        previous_status=previous_status,
        status=str(data.get("status")),
        action="update",
    )
    details = {
        "id": record.document.frontmatter.id,
        "changed": True,
        "dry_run": dry_run,
        "changes": changes,
        "previous": previous,
        "updated": updated,
    }
    if target_path != previous_path:
        details["previous_relative_path"] = previous_path.relative_to(config.vault_path).as_posix()
        details["relative_path"] = target_path.relative_to(config.vault_path).as_posix()
    return LifecycleResult(
        command="memory update",
        mutations=() if dry_run else (mutation,),
        details=details,
    )


def review_batch_action(
    config: MemoryConfig,
    action: str,
    memory_ids: Sequence[str],
    *,
    reason: Optional[str] = None,
    dry_run: bool = False,
    override_unsafe: bool = False,
) -> ReviewBatchResult:
    """Apply an explicit batch review action to pending agent-generated memories."""

    selected_action = action.strip().lower()
    if selected_action not in {"approve", "reject"}:
        raise ValueError("review action must be approve or reject")
    if not memory_ids:
        raise ValueError("at least one memory id is required")

    if dry_run:
        results = _plan_review_batch_action(
            config,
            selected_action,
            memory_ids,
            override_unsafe=override_unsafe,
        )
    else:
        with vault_lock(config):
            results = _apply_review_batch_action_unlocked(
                config,
                selected_action,
                memory_ids,
                reason=reason,
                override_unsafe=override_unsafe,
            )

    details: dict[str, Any] = {"requested_ids": list(memory_ids)}
    if selected_action == "approve":
        details["override_unsafe"] = override_unsafe
    return ReviewBatchResult(
        command=f"review {selected_action}",
        action=selected_action,
        dry_run=dry_run,
        reason=reason,
        results=tuple(results),
        details=details,
    )


def _plan_review_batch_action(
    config: MemoryConfig,
    action: str,
    memory_ids: Sequence[str],
    *,
    override_unsafe: bool,
) -> tuple[ReviewBatchItemResult, ...]:
    review_items = {item.memory_id: item for item in review_queue(config).items}
    records_by_id = _records_by_id(config)
    results: list[ReviewBatchItemResult] = []
    for memory_id in memory_ids:
        error = _review_item_error(
            memory_id, review_items=review_items, records_by_id=records_by_id
        )
        if error is not None:
            results.append(
                _review_error_result(memory_id, action=action, dry_run=True, error=error)
            )
            continue
        item = review_items[memory_id]
        unsafe_error = _unsafe_approval_error(action, item, override_unsafe=override_unsafe)
        if unsafe_error is not None:
            results.append(
                _review_error_result(
                    memory_id,
                    action=action,
                    dry_run=True,
                    error=unsafe_error,
                    item=item,
                )
            )
            continue
        results.append(
            ReviewBatchItemResult(
                memory_id=memory_id,
                action=action,
                ok=True,
                dry_run=True,
                planned=True,
                previous_status=item.status,
                status=_review_action_status(action, current_status=item.status),
                path=item.path,
                relative_path=item.relative_path,
                risk_flags=item.risk_flags,
            )
        )
    return tuple(results)


def _apply_review_batch_action_unlocked(
    config: MemoryConfig,
    action: str,
    memory_ids: Sequence[str],
    *,
    reason: Optional[str],
    override_unsafe: bool,
) -> tuple[ReviewBatchItemResult, ...]:
    review_items = {item.memory_id: item for item in review_queue(config).items}
    records_by_id = _records_by_id(config)
    results: list[ReviewBatchItemResult] = []
    for memory_id in memory_ids:
        error = _review_item_error(
            memory_id, review_items=review_items, records_by_id=records_by_id
        )
        if error is not None:
            results.append(
                _review_error_result(memory_id, action=action, dry_run=False, error=error)
            )
            continue
        item = review_items[memory_id]
        unsafe_error = _unsafe_approval_error(action, item, override_unsafe=override_unsafe)
        if unsafe_error is not None:
            results.append(
                _review_error_result(
                    memory_id,
                    action=action,
                    dry_run=False,
                    error=unsafe_error,
                    item=item,
                )
            )
            continue
        try:
            result = _apply_one_review_action_unlocked(
                config,
                action,
                memory_id,
                reason=reason,
            )
        except Exception as exc:
            results.append(
                _review_error_result(
                    memory_id,
                    action=action,
                    dry_run=False,
                    error={"code": "review_action_failed", "message": str(exc)},
                    item=item,
                )
            )
            continue
        mutation = result.mutations[0] if result.mutations else None
        results.append(
            ReviewBatchItemResult(
                memory_id=memory_id,
                action=action,
                ok=True,
                dry_run=False,
                planned=False,
                previous_status=mutation.previous_status if mutation else item.status,
                status=mutation.status
                if mutation
                else _review_action_status(action, current_status=item.status),
                path=mutation.path if mutation else item.path,
                relative_path=mutation.relative_path if mutation else item.relative_path,
                risk_flags=item.risk_flags,
                mutation=mutation,
            )
        )
    return tuple(results)


def _apply_one_review_action_unlocked(
    config: MemoryConfig,
    action: str,
    memory_id: str,
    *,
    reason: Optional[str],
) -> LifecycleResult:
    if action == "approve":
        result = _mark_status_unlocked(
            config,
            memory_id,
            LifecycleStatus.ACTIVE.value,
            reason=reason,
            _action="approve",
        )
        return LifecycleResult(
            command="approve", mutations=result.mutations, details=result.details
        )
    if action == "reject":
        result = _mark_status_unlocked(
            config,
            memory_id,
            LifecycleStatus.REJECTED.value,
            reason=reason,
            _action="reject",
        )
        return LifecycleResult(command="reject", mutations=result.mutations, details=result.details)
    raise ValueError(f"unsupported review action: {action}")


def _review_item_error(
    memory_id: str,
    *,
    review_items: Mapping[str, ReviewItem],
    records_by_id: Mapping[str, _MemoryRecord],
) -> Optional[dict[str, str]]:
    if memory_id in review_items:
        return None
    if memory_id in records_by_id:
        return {
            "code": "not_pending_review_item",
            "message": f"memory is not a pending agent review item: {memory_id}",
        }
    return {
        "code": "memory_not_found",
        "message": f"memory not found: {memory_id}",
    }


def _unsafe_approval_error(
    action: str,
    item: ReviewItem,
    *,
    override_unsafe: bool,
) -> Optional[dict[str, str]]:
    if action != "approve" or override_unsafe or not has_unsafe_recall_risk(item.risk_flags):
        return None
    return {
        "code": "unsafe_approval_blocked",
        "message": f"unsafe review item requires --override-unsafe: {item.memory_id}",
    }


def _review_error_result(
    memory_id: str,
    *,
    action: str,
    dry_run: bool,
    error: dict[str, str],
    item: Optional[ReviewItem] = None,
) -> ReviewBatchItemResult:
    return ReviewBatchItemResult(
        memory_id=memory_id,
        action=action,
        ok=False,
        dry_run=dry_run,
        planned=False,
        previous_status=item.status if item else None,
        status=item.status if item else None,
        path=item.path if item else None,
        relative_path=item.relative_path if item else None,
        risk_flags=item.risk_flags if item else (),
        error=error,
    )


def _review_action_status(action: str, *, current_status: str) -> str:
    if action == "approve":
        return LifecycleStatus.ACTIVE.value
    if action == "reject":
        return LifecycleStatus.REJECTED.value
    return current_status


def review_queue(config: MemoryConfig) -> ReviewQueue:
    """Return pending agent-generated memories for explicit review."""

    items: list[ReviewItem] = []
    records = _iter_memory_records(config)
    record_index = {record.document.frontmatter.id: record for record in records}
    signature_index = _duplicate_signature_index(records)
    for record in records:
        frontmatter = record.document.frontmatter
        author = frontmatter.author
        if frontmatter.status != LifecycleStatus.PENDING:
            continue
        if author is None or author.kind != AuthorKind.AGENT:
            continue
        duplicate_candidates = _duplicate_candidates(config, record, signature_index)
        contradiction_candidates = _contradiction_candidates(config, record, records, record_index)
        importance = _review_importance(
            frontmatter,
            has_duplicate_candidates=bool(duplicate_candidates),
            has_contradiction_candidates=bool(contradiction_candidates),
        )
        risk_flags = _review_risk_flags(
            config,
            frontmatter,
            body=record.body,
            importance=importance,
            has_duplicate_candidates=bool(duplicate_candidates),
            has_contradiction_candidates=bool(contradiction_candidates),
        )
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
                importance=importance,
                body=record.body,
                updated_at=frontmatter.updated_at.isoformat(),
                risk_flags=risk_flags,
                recommended_action=_review_recommended_action(
                    config,
                    frontmatter,
                    risk_flags=risk_flags,
                ),
                duplicate_candidates=duplicate_candidates,
                contradiction_candidates=contradiction_candidates,
            )
        )
    items.sort(key=lambda item: (item.updated_at, item.relative_path.as_posix()))
    return ReviewQueue(config=config, items=tuple(items))


def _source_groups(items: Sequence[ReviewItem]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        source = item.source or {}
        source_key = str(source.get("path") or source.get("url") or "missing_source")
        group = groups.setdefault(
            source_key,
            {
                "source": source or None,
                "item_count": 0,
                "memory_ids": [],
                "items": [],
            },
        )
        group["item_count"] += 1
        group["memory_ids"].append(item.memory_id)
        group["items"].append(item.to_dict())
    return sorted(groups.values(), key=lambda group: str(group["source"] or ""))


def _curation_recommended_action(item: ReviewItem) -> str:
    flags = set(item.risk_flags)
    if item.contradiction_candidates:
        return "inspect_contradiction"
    if item.duplicate_candidates:
        if any(
            candidate.status == LifecycleStatus.ACTIVE.value
            for candidate in item.duplicate_candidates
        ):
            return "merge_or_reject_duplicate"
        return "inspect_duplicate"
    if flags & {"low_confidence", "missing_confidence", "missing_source"}:
        return "inspect"
    if not flags and item.recommended_action == "approve":
        return "approve"
    return "inspect"


def _curation_metadata(item: ReviewItem, *, recommended_action: str) -> dict[str, Any]:
    return {
        "proposal_only": True,
        "recommended_action": recommended_action,
        "reason": _curation_reason(item, recommended_action),
        "duplicate_candidate_count": len(item.duplicate_candidates),
        "contradiction_candidate_count": len(item.contradiction_candidates),
        "signals": _curation_signals(item),
    }


def _curation_reason(item: ReviewItem, recommended_action: str) -> str:
    if item.contradiction_candidates:
        return "likely_contradiction_requires_human_inspection"
    if item.duplicate_candidates:
        if any(
            candidate.status == LifecycleStatus.ACTIVE.value
            for candidate in item.duplicate_candidates
        ):
            return "likely_duplicate_of_active_memory"
        return "possible_duplicate_requires_human_inspection"
    flags = set(item.risk_flags)
    if flags & {"low_confidence", "missing_confidence", "missing_source"}:
        return "review_risk_flags_require_inspection"
    if recommended_action == "approve":
        return "no_curation_risks_detected"
    return "no_high_confidence_curation_action"


def _curation_signals(item: ReviewItem) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for candidate in item.duplicate_candidates:
        signal = {
            "kind": "duplicate",
            "id": candidate.memory_id,
            "reason": candidate.match_reason,
            "score": candidate.score if candidate.score is not None else 1.0,
            "confidence": candidate.confidence if candidate.confidence is not None else 0.95,
        }
        signals.append(signal)
    for candidate in item.contradiction_candidates:
        signal = {
            "kind": "contradiction",
            "id": candidate.memory_id,
            "reason": candidate.match_reason,
            "confidence": candidate.confidence if candidate.confidence is not None else 0.95,
        }
        if candidate.evidence is not None:
            signal["evidence"] = dict(candidate.evidence)
        signals.append(signal)
    return signals


def _records_by_id(config: MemoryConfig) -> dict[str, _MemoryRecord]:
    return {record.document.frontmatter.id: record for record in _iter_memory_records(config)}


def _review_risk_flags(
    config: MemoryConfig,
    frontmatter: MemoryFrontmatter,
    *,
    body: str = "",
    importance: Optional[ReviewImportance] = None,
    has_duplicate_candidates: bool = False,
    has_contradiction_candidates: bool = False,
) -> tuple[str, ...]:
    flags: list[str] = []
    flags.extend(_memory_safety_flags(config, frontmatter, body=body))
    confidence = frontmatter.confidence
    if confidence is None:
        flags.append("missing_confidence")
    elif confidence < config.agent_policy.min_pending_confidence:
        flags.append("low_confidence")
    elif confidence < config.agent_policy.min_active_confidence:
        flags.append("needs_human_judgment")
    if frontmatter.source is None:
        flags.append("missing_source")
    if _outgoing_contradiction_targets(frontmatter) or has_contradiction_candidates:
        flags.append("has_contradictions")
    if has_duplicate_candidates:
        flags.append("possible_duplicate")
    if importance is not None and importance.score >= _HIGH_IMPORTANCE_THRESHOLD:
        flags.append("high_importance")
    return normalize_risk_flags(flags)


def _review_recommended_action(
    config: MemoryConfig,
    frontmatter: MemoryFrontmatter,
    *,
    risk_flags: Optional[Sequence[str]] = None,
) -> str:
    flags = normalize_risk_flags(risk_flags or _review_risk_flags(config, frontmatter))
    if (
        has_unsafe_recall_risk(flags)
        or "low_confidence" in flags
        or "missing_source" in flags
        or "missing_confidence" in flags
        or "possible_duplicate" in flags
        or "has_contradictions" in flags
    ):
        return "inspect"
    if (
        frontmatter.confidence is not None
        and frontmatter.confidence >= config.agent_policy.min_active_confidence
    ):
        return "approve"
    return "inspect"


def _memory_safety_flags(
    config: MemoryConfig,
    frontmatter: MemoryFrontmatter,
    *,
    body: str,
) -> tuple[str, ...]:
    frontmatter_flags = normalize_risk_flags(frontmatter.risk_flags)
    source_flags = _source_safety_flags(config, frontmatter.source)
    scanned = scan_text(body, field="memory")
    observation_scans = [
        scan_text(observation.text, field="observation") for observation in frontmatter.observations
    ]
    safety = merge_scan_results(scanned, *observation_scans)
    return normalize_risk_flags((*frontmatter_flags, *source_flags, *safety.risk_flags))


def _source_safety_flags(config: MemoryConfig, source: Any) -> tuple[str, ...]:
    if source is None:
        return ()
    source_payload = (
        source.model_dump(mode="json", exclude_none=True) if hasattr(source, "model_dump") else {}
    )
    flags = list(normalize_risk_flags(source_payload.get("risk_flags")))
    flags.extend(scan_metadata(source_payload).risk_flags)
    source_path = source_payload.get("path")
    if isinstance(source_path, str) and source_path:
        candidate = config.vault_path / source_path
        if candidate.is_file() and candidate.resolve().is_relative_to(config.vault_path.resolve()):
            try:
                text = candidate.read_text(encoding="utf-8")
                frontmatter = _source_frontmatter_mapping(text)
            except Exception:
                frontmatter = {}
            flags.extend(normalize_risk_flags(frontmatter.get("risk_flags")))
            flags.extend(scan_metadata(frontmatter).risk_flags)
    return normalize_risk_flags(flags)


def _source_frontmatter_mapping(text: str) -> Mapping[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    payload = yaml.safe_load(parts[0][4:]) or {}
    return payload if isinstance(payload, Mapping) else {}


def _review_importance(
    frontmatter: MemoryFrontmatter,
    *,
    has_duplicate_candidates: bool = False,
    has_contradiction_candidates: bool = False,
) -> ReviewImportance:
    raw_importance = _frontmatter_extra(frontmatter, "importance")
    if raw_importance is not _MISSING:
        score, invalid_reason = _frontmatter_importance_score(raw_importance)
        if score is not None:
            return ReviewImportance(
                score=score,
                source="frontmatter",
                reasons=("frontmatter_importance",),
            )
        return _proposed_review_importance(
            frontmatter,
            has_duplicate_candidates=has_duplicate_candidates,
            has_contradiction_candidates=has_contradiction_candidates,
            extra_reasons=(invalid_reason or "invalid_frontmatter_importance",),
        )
    return _proposed_review_importance(
        frontmatter,
        has_duplicate_candidates=has_duplicate_candidates,
        has_contradiction_candidates=has_contradiction_candidates,
    )


def _proposed_review_importance(
    frontmatter: MemoryFrontmatter,
    *,
    has_duplicate_candidates: bool,
    has_contradiction_candidates: bool,
    extra_reasons: tuple[str, ...] = (),
) -> ReviewImportance:
    memory_type = frontmatter.type.value
    scope = frontmatter.scope.value
    score = {
        "decision": 0.62,
        "project_context": 0.58,
        "preference": 0.55,
        "task": 0.5,
        "fact": 0.45,
        "conversation_summary": 0.4,
    }.get(memory_type, 0.45)
    reasons: list[str] = [*extra_reasons, f"type:{memory_type}", f"scope:{scope}"]

    if scope == "global":
        score += 0.12
    elif scope == "project":
        score += 0.1

    confidence = frontmatter.confidence
    if confidence is None:
        reasons.append("missing_confidence")
    elif confidence >= 0.9:
        score += 0.12
        reasons.append("confidence:high")
    elif confidence >= 0.75:
        score += 0.08
        reasons.append("confidence:medium")
    elif confidence >= 0.55:
        score += 0.03
        reasons.append("confidence:reviewable")
    else:
        score -= 0.08
        reasons.append("confidence:low")

    if frontmatter.source is None:
        score -= 0.05
        reasons.append("missing_source")
    else:
        score += 0.05
        reasons.append("has_source")

    if has_contradiction_candidates or _outgoing_contradiction_targets(frontmatter):
        score -= 0.15
        reasons.append("has_contradictions")
    if has_duplicate_candidates:
        score -= 0.1
        reasons.append("possible_duplicate")

    return ReviewImportance(
        score=_bounded_score(score),
        source="proposed",
        reasons=tuple(reasons),
    )


def _frontmatter_extra(frontmatter: MemoryFrontmatter, key: str) -> Any:
    extra = getattr(frontmatter, "model_extra", None) or {}
    return extra.get(key, _MISSING)


def _frontmatter_importance_score(value: Any) -> tuple[Optional[float], Optional[str]]:
    if isinstance(value, dict):
        if "score" not in value:
            return None, "frontmatter_importance_missing_score"
        value = value["score"]
    if isinstance(value, bool):
        return None, "invalid_frontmatter_importance"
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None, "invalid_frontmatter_importance"
    if not math.isfinite(score):
        return None, "invalid_frontmatter_importance"
    if score < 0 or score > 1:
        return None, "frontmatter_importance_out_of_range"
    return score, None


def _bounded_score(score: float) -> float:
    return round(max(0.0, min(1.0, score)), 2)


def _contradiction_candidates(
    config: MemoryConfig,
    record: _MemoryRecord,
    records: Sequence[_MemoryRecord],
    record_index: dict[str, _MemoryRecord],
) -> tuple[ContradictionCandidate, ...]:
    frontmatter = record.document.frontmatter
    candidates: dict[tuple[str, str], ContradictionCandidate] = {}
    for target_id in _outgoing_contradiction_targets(frontmatter):
        _add_contradiction_candidate(
            config,
            candidates,
            memory_id=target_id,
            target_record=record_index.get(target_id),
            relation_direction="outgoing",
        )

    for candidate_record in records:
        candidate_frontmatter = candidate_record.document.frontmatter
        if candidate_frontmatter.id == frontmatter.id:
            continue
        if candidate_frontmatter.status.value not in _DUPLICATE_CANDIDATE_STATUSES:
            continue
        if frontmatter.id not in _outgoing_contradiction_targets(candidate_frontmatter):
            continue
        _add_contradiction_candidate(
            config,
            candidates,
            memory_id=candidate_frontmatter.id,
            target_record=candidate_record,
            relation_direction="incoming",
        )
    for candidate_record in records:
        candidate_frontmatter = candidate_record.document.frontmatter
        if candidate_frontmatter.id == frontmatter.id:
            continue
        if candidate_frontmatter.status.value not in _DUPLICATE_CANDIDATE_STATUSES:
            continue
        if any(
            candidate.memory_id == candidate_frontmatter.id for candidate in candidates.values()
        ):
            continue
        heuristic = _heuristic_contradiction_candidate(config, record, candidate_record)
        if heuristic is not None:
            candidates[(heuristic.memory_id, heuristic.relation_direction)] = heuristic
    return tuple(candidates.values())


def _add_contradiction_candidate(
    config: MemoryConfig,
    candidates: dict[tuple[str, str], ContradictionCandidate],
    *,
    memory_id: str,
    target_record: Optional[_MemoryRecord],
    relation_direction: str,
) -> None:
    candidate_key = (memory_id, relation_direction)
    if candidate_key in candidates:
        return
    target_frontmatter = target_record.document.frontmatter if target_record else None
    candidates[candidate_key] = ContradictionCandidate(
        memory_id=memory_id,
        relative_path=target_record.path.relative_to(config.vault_path) if target_record else None,
        memory_type=target_frontmatter.type.value if target_frontmatter else None,
        status=target_frontmatter.status.value if target_frontmatter else None,
        relation_direction=relation_direction,
        match_reason="explicit_contradicts_relation",
    )


def _outgoing_contradiction_targets(frontmatter: MemoryFrontmatter) -> tuple[str, ...]:
    targets = list(frontmatter.contradicts)
    targets.extend(
        relation.target
        for relation in frontmatter.relations
        if relation.type == RelationType.CONTRADICTS
    )
    return tuple(dict.fromkeys(targets))


def _heuristic_contradiction_candidate(
    config: MemoryConfig,
    record: _MemoryRecord,
    candidate_record: _MemoryRecord,
) -> Optional[ContradictionCandidate]:
    for pending_field, pending_text in _contradiction_text_values(record):
        for candidate_field, candidate_text in _contradiction_text_values(candidate_record):
            signal = detect_opposite_claim(pending_text, candidate_text)
            if signal is None:
                continue
            candidate_frontmatter = candidate_record.document.frontmatter
            return ContradictionCandidate(
                memory_id=candidate_frontmatter.id,
                relative_path=candidate_record.path.relative_to(config.vault_path),
                memory_type=candidate_frontmatter.type.value,
                status=candidate_frontmatter.status.value,
                relation_direction="inferred",
                match_reason=signal.match_reason,
                confidence=signal.confidence,
                matched_fields={
                    "pending": (pending_field,),
                    "candidate": (candidate_field,),
                },
                evidence=signal.evidence(),
            )
    return None


def _contradiction_text_values(record: _MemoryRecord) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    if record.body.strip():
        values.append(("body", record.body))
    for observation in record.document.frontmatter.observations:
        if observation.text.strip():
            values.append(("observation", observation.text))
    return tuple(dict.fromkeys(values))


def _duplicate_candidates(
    config: MemoryConfig,
    record: _MemoryRecord,
    signature_index: dict[str, list[tuple[_MemoryRecord, str]]],
) -> tuple[DuplicateCandidate, ...]:
    frontmatter = record.document.frontmatter
    candidates: dict[str, DuplicateCandidate] = {}
    for signature, pending_field in _duplicate_text_signatures(record):
        for candidate_record, candidate_field in signature_index.get(signature, []):
            candidate_frontmatter = candidate_record.document.frontmatter
            if candidate_frontmatter.id == frontmatter.id:
                continue
            if candidate_frontmatter.status.value not in _DUPLICATE_CANDIDATE_STATUSES:
                continue
            existing = candidates.get(candidate_frontmatter.id)
            if existing is None:
                candidates[candidate_frontmatter.id] = DuplicateCandidate(
                    memory_id=candidate_frontmatter.id,
                    relative_path=candidate_record.path.relative_to(config.vault_path),
                    memory_type=candidate_frontmatter.type.value,
                    status=candidate_frontmatter.status.value,
                    match_reason="normalized_content_exact_match",
                    signature=signature,
                    matched_fields={
                        "pending": (pending_field,),
                        "candidate": (candidate_field,),
                    },
                )
                continue
            if existing.signature != signature:
                continue
            candidates[candidate_frontmatter.id] = DuplicateCandidate(
                memory_id=existing.memory_id,
                relative_path=existing.relative_path,
                memory_type=existing.memory_type,
                status=existing.status,
                match_reason=existing.match_reason,
                signature=existing.signature,
                matched_fields={
                    "pending": _sorted_tuple((*existing.matched_fields["pending"], pending_field)),
                    "candidate": _sorted_tuple(
                        (*existing.matched_fields["candidate"], candidate_field)
                    ),
                },
            )
    for candidate_record in _iter_near_duplicate_records(record, signature_index):
        candidate_frontmatter = candidate_record.document.frontmatter
        if candidate_frontmatter.id in candidates:
            continue
        signal = _near_duplicate_candidate_signal(record, candidate_record)
        if signal is None:
            continue
        pending_field, candidate_field, match = signal
        candidates[candidate_frontmatter.id] = DuplicateCandidate(
            memory_id=candidate_frontmatter.id,
            relative_path=candidate_record.path.relative_to(config.vault_path),
            memory_type=candidate_frontmatter.type.value,
            status=candidate_frontmatter.status.value,
            match_reason="normalized_content_near_match",
            signature=match.signature,
            matched_fields={
                "pending": (pending_field,),
                "candidate": (candidate_field,),
            },
            score=match.score,
            confidence=match.confidence,
        )
    return tuple(
        sorted(
            candidates.values(),
            key=lambda candidate: (
                candidate.status != LifecycleStatus.ACTIVE.value,
                candidate.relative_path.as_posix(),
                candidate.memory_id,
            ),
        )
    )


def _duplicate_signature_index(
    records: Sequence[_MemoryRecord],
) -> dict[str, list[tuple[_MemoryRecord, str]]]:
    index: dict[str, list[tuple[_MemoryRecord, str]]] = {}
    for record in records:
        for signature, field in _duplicate_text_signatures(record):
            index.setdefault(signature, []).append((record, field))
    return index


def _iter_near_duplicate_records(
    record: _MemoryRecord,
    signature_index: dict[str, list[tuple[_MemoryRecord, str]]],
) -> tuple[_MemoryRecord, ...]:
    records: dict[str, _MemoryRecord] = {}
    current_id = record.document.frontmatter.id
    for indexed_records in signature_index.values():
        for candidate_record, _field in indexed_records:
            candidate_frontmatter = candidate_record.document.frontmatter
            if candidate_frontmatter.id == current_id:
                continue
            if candidate_frontmatter.status.value not in _DUPLICATE_CANDIDATE_STATUSES:
                continue
            records[candidate_frontmatter.id] = candidate_record
    return tuple(records.values())


def _near_duplicate_candidate_signal(
    record: _MemoryRecord, candidate_record: _MemoryRecord
) -> Optional[tuple[str, str, Any]]:
    best: Optional[tuple[str, str, Any]] = None
    for pending_field, pending_text in _duplicate_text_values(record):
        for candidate_field, candidate_text in _duplicate_text_values(candidate_record):
            match = near_duplicate_text(pending_text, candidate_text)
            if match is None:
                continue
            if _duplicate_text_signature(pending_text) == _duplicate_text_signature(candidate_text):
                continue
            if best is None or match.score > best[2].score:
                best = (pending_field, candidate_field, match)
    return best


def _duplicate_text_signatures(record: _MemoryRecord) -> tuple[tuple[str, str], ...]:
    signatures: list[tuple[str, str]] = []
    for field, value in _duplicate_text_values(record):
        signature = _duplicate_text_signature(value)
        if signature is not None:
            signatures.append((signature, field))
    return tuple(dict.fromkeys(signatures))


def _duplicate_text_values(record: _MemoryRecord) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    frontmatter = record.document.frontmatter
    if record.body.strip():
        values.append(("body", record.body))
    for observation in frontmatter.observations:
        if observation.text.strip():
            values.append(("observation", observation.text))
    if frontmatter.title:
        values.append(("title", frontmatter.title))
    for alias in frontmatter.aliases:
        if alias.strip():
            values.append(("alias", alias))
    return tuple(dict.fromkeys(values))


def _duplicate_text_signature(text: str) -> Optional[str]:
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _sorted_tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _validate_update_options(options: MemoryUpdateOptions) -> None:
    if options.clear_project and options.project is not None:
        raise ValueError("--project and --clear-project cannot be used together")
    if options.clear_confidence and options.confidence is not None:
        raise ValueError("--confidence and --clear-confidence cannot be used together")
    if options.clear_tags and options.tags is not None:
        raise ValueError("--tag and --clear-tags cannot be used together")
    if options.clear_title and options.title is not None:
        raise ValueError("--title and --clear-title cannot be used together")
    if options.confidence is not None and not 0 <= float(options.confidence) <= 1:
        raise ValueError("--confidence must be between 0 and 1")
    if not any(
        (
            options.memory_type is not None,
            options.scope is not None,
            options.project is not None,
            options.clear_project,
            options.status is not None,
            options.confidence is not None,
            options.clear_confidence,
            options.tags is not None,
            options.clear_tags,
            options.title is not None,
            options.clear_title,
            options.text is not None,
        )
    ):
        raise ValueError("at least one memory field must be provided")


def _target_path_for_update(
    config: MemoryConfig, current_path: Path, memory_type: MemoryType
) -> Path:
    target_dir = config.memory_root / MEMORY_TYPE_DIRECTORIES[memory_type]
    target_path = target_dir / current_path.name
    if target_path != current_path and target_path.exists():
        raise ValueError(
            f"target memory path already exists: {target_path.relative_to(config.vault_path)}"
        )
    return target_path


def _clean_optional_text(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    return cleaned


def _normalize_tag_updates(tags: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        item = str(tag).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _retag_observations(data: dict[str, Any], *, old_type: str, new_type: str) -> None:
    observations = data.get("observations")
    if not isinstance(observations, list):
        return
    for observation in observations:
        if isinstance(observation, dict) and observation.get("category") == old_type:
            observation["category"] = new_type


def _update_observation_text(data: dict[str, Any], *, old_text: str, new_text: str) -> None:
    observations = data.get("observations")
    if not isinstance(observations, list):
        return
    if not observations:
        return
    first = observations[0]
    if isinstance(first, dict) and str(first.get("text") or "").strip() == old_text:
        first["text"] = new_text


def _update_observation_confidence(
    data: dict[str, Any],
    *,
    old_confidence: Any,
    new_confidence: Optional[float],
) -> None:
    observations = data.get("observations")
    if not isinstance(observations, list):
        return
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if observation.get("confidence") in (old_confidence, None):
            observation["confidence"] = new_confidence


def _update_summary(
    data: Mapping[str, Any], body: str, path: Path, *, config: MemoryConfig
) -> dict[str, Any]:
    return {
        "id": str(data.get("id")),
        "type": str(data.get("type")),
        "scope": str(data.get("scope")),
        "project": data.get("project"),
        "status": str(data.get("status")),
        "confidence": data.get("confidence"),
        "tags": _string_list(data.get("tags")),
        "title": data.get("title"),
        "text": body.strip(),
        "relative_path": path.relative_to(config.vault_path).as_posix(),
    }


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
        raise ValueError(
            f"{path}: memory Markdown must start with YAML frontmatter delimited by ---"
        )
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
        "actor": "memora",
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
    "ReviewBatchItemResult",
    "ReviewBatchResult",
    "ReviewImportance",
    "ReviewItem",
    "ReviewQueue",
    "review_batch_action",
    "review_queue",
]
