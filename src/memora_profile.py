"""Bounded in-memory profile context for active canonical memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

from config import MemoryConfig
from indexer import estimate_tokens
from markdown import aliases as presentation_aliases
from markdown import wikilink_for_memory
from safety import has_unsafe_recall_risk, scan_text
from schema import LifecycleStatus, MemoryDocument, MemoryScope, MemoryType, validate_vault

PROFILE_SCHEMA_VERSION = 1
PROFILE_TYPES = {"user", "project"}
TYPE_ORDER = (
    MemoryType.DECISION.value,
    MemoryType.PREFERENCE.value,
    MemoryType.PROJECT_CONTEXT.value,
    MemoryType.FACT.value,
    MemoryType.TASK.value,
    MemoryType.SOURCE_EXTRACT.value,
    MemoryType.CONVERSATION_SUMMARY.value,
)
TYPE_TITLES = {
    MemoryType.DECISION.value: "Decisions",
    MemoryType.PREFERENCE.value: "Preferences",
    MemoryType.PROJECT_CONTEXT.value: "Project Context",
    MemoryType.FACT.value: "Facts",
    MemoryType.TASK.value: "Tasks",
    MemoryType.SOURCE_EXTRACT.value: "Source Extracts",
    MemoryType.CONVERSATION_SUMMARY.value: "Conversation Summaries",
}
PROFILE_INJECTION_CITATION_PREFIX = "P"


@dataclass(frozen=True)
class ProfileCandidate:
    """One matching canonical memory before budget packing."""

    memory_id: str
    memory_type: str
    summary: str
    relative_path: Path


@dataclass(frozen=True)
class ProfileItem:
    """One selected memory rendered as a profile bullet."""

    memory_id: str
    memory_type: str
    summary: str
    citation_key: str
    relative_path: Path

    def citation(self) -> dict[str, Any]:
        return {
            "key": self.citation_key,
            "id": self.memory_id,
            "path": self.relative_path.as_posix(),
            "type": self.memory_type,
        }


@dataclass(frozen=True)
class ProfileResult:
    """Generated profile context selected for build-context."""

    config: MemoryConfig
    profile_type: str
    project: Optional[str]
    budget: int
    generated_at: datetime
    markdown: str
    items: tuple[ProfileItem, ...]
    truncated: bool

    @property
    def citations(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.citation() for item in self.items)

    @property
    def used_tokens_estimate(self) -> int:
        return estimate_tokens(self.markdown) if self.markdown.strip() else 0


def generate_profile_context(
    config: MemoryConfig,
    profile_type: str = "user",
    project: Optional[str] = None,
    budget: Optional[int] = None,
    now: Optional[datetime] = None,
) -> ProfileResult:
    """Render deterministic generated profile context without writing files."""

    return _generate_profile_result(
        config,
        profile_type=profile_type,
        project=project,
        budget=budget,
        now=now,
    )


def build_context_profile_payload(
    config: MemoryConfig,
    *,
    requested: bool,
    request_sources: Sequence[str] = (),
    project: Optional[str] = None,
    task_budget: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return bounded generated profile context metadata for build-context payloads."""

    selected_project = _clean_optional(project)
    profile_type = "project" if selected_project else "user"
    profile_budget = _context_profile_budget(
        config, profile_type=profile_type, task_budget=task_budget
    )
    payload: dict[str, Any] = {
        "included": False,
        "requested": bool(requested),
        "request_sources": list(request_sources),
        "reason": "profile_injection_disabled",
        "profile_type": profile_type,
        "project": selected_project,
        "budget": profile_budget,
        "used_tokens_estimate": 0,
        "memory_count": 0,
        "citations": [],
    }

    if not requested:
        return payload
    if not config.profile.enabled:
        payload["reason"] = "profile_disabled"
        return payload

    try:
        profile = generate_profile_context(
            config,
            profile_type=profile_type,
            project=selected_project,
            budget=profile_budget,
            now=now,
        )
    except Exception as exc:
        payload.update(
            {
                "reason": "profile_unavailable",
                "error": str(exc),
            }
        )
        return payload

    payload.update(
        {
            "profile_type": profile.profile_type,
            "project": profile.project,
            "budget": profile.budget,
            "used_tokens_estimate": profile.used_tokens_estimate,
            "memory_count": len(profile.items),
            "source_memory_ids": [item.memory_id for item in profile.items],
            "generated_at": profile.generated_at.isoformat(),
            "truncated": profile.truncated,
        }
    )
    if not profile.items:
        payload["reason"] = "no_profile_data"
        return payload

    markdown, citations = _namespace_profile_citations(profile.markdown, profile.citations)
    payload.update(
        {
            "included": True,
            "reason": "included",
            "markdown": markdown,
            "citations": citations,
        }
    )
    return payload


def _generate_profile_result(
    config: MemoryConfig,
    *,
    profile_type: str,
    project: Optional[str],
    budget: Optional[int],
    now: Optional[datetime],
) -> ProfileResult:
    """Build a profile result without persisting generated context."""

    selected_profile_type = _validate_profile_type(profile_type)
    selected_project = _clean_optional(project)
    if selected_profile_type == "project" and selected_project is None:
        raise ValueError("project profile requires project")
    if selected_profile_type == "user" and selected_project is not None:
        raise ValueError("user profile does not accept project")

    if not config.profile.enabled:
        raise ValueError("profile generation is disabled")

    selected_budget = _validate_budget(
        _configured_budget(config, selected_profile_type) if budget is None else budget
    )
    generated_at = _normalize_now(now)

    report = validate_vault(config.vault_path)
    if report.issues:
        first_issue = report.issues[0]
        raise ValueError(
            f"cannot build profile from invalid vault: {first_issue.path}: {first_issue.message}"
        )

    candidates = _select_candidates(
        report.documents,
        config=config,
        profile_type=selected_profile_type,
        project=selected_project,
    )
    selected_candidates, truncated = _fit_candidates(
        candidates,
        profile_type=selected_profile_type,
        project=selected_project,
        generated_at=generated_at,
        budget=selected_budget,
    )
    items = _items_from_candidates(selected_candidates)
    markdown = render_profile_markdown(
        profile_type=selected_profile_type,
        project=selected_project,
        generated_at=generated_at,
        token_budget=selected_budget,
        items=items,
    )
    if estimate_tokens(markdown) > selected_budget:
        raise ValueError("budget is too small to render required profile frontmatter")

    return ProfileResult(
        config=config,
        profile_type=selected_profile_type,
        project=selected_project,
        budget=selected_budget,
        generated_at=generated_at,
        markdown=markdown,
        items=items,
        truncated=truncated,
    )


def render_profile_markdown(
    *,
    profile_type: str,
    project: Optional[str],
    generated_at: datetime,
    token_budget: int,
    items: Sequence[ProfileItem],
) -> str:
    """Render profile Markdown with generated, non-canonical frontmatter."""

    title = _profile_title(profile_type=profile_type, project=project)
    frontmatter = {
        "kind": "profile",
        "schema_version": PROFILE_SCHEMA_VERSION,
        "title": title,
        "aliases": presentation_aliases(
            title, _profile_alias(profile_type=profile_type, project=project)
        ),
        "profile_type": profile_type,
        "project": project,
        "generated_at": generated_at.isoformat(),
        "source_memory_ids": [item.memory_id for item in items],
        "token_budget": token_budget,
        "status": "generated",
    }
    rendered_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    lines = [
        "---",
        rendered_yaml,
        "---",
        "",
        f"# {title}",
        "",
        "Generated context, not canonical memory.",
        f"Generated at: {generated_at.isoformat()}",
        f"Selected active memories: {len(items)}",
        "",
    ]

    if not items:
        lines.extend(["No active canonical memories matched this profile.", ""])
    else:
        for memory_type in _ordered_types(items):
            lines.append(f"## {TYPE_TITLES.get(memory_type, _title_from_type(memory_type))}")
            for item in (item for item in items if item.memory_type == memory_type):
                lines.append(f"- {item.summary} [{item.citation_key}]")
            lines.append("")

        lines.append("## Citations")
        for item in items:
            wikilink = wikilink_for_memory(item.memory_id, item.relative_path)
            lines.append(f"- [{item.citation_key}] {wikilink} ({item.relative_path.as_posix()})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _select_candidates(
    documents: Sequence[MemoryDocument],
    *,
    config: MemoryConfig,
    profile_type: str,
    project: Optional[str],
) -> tuple[ProfileCandidate, ...]:
    selected: list[MemoryDocument] = []
    for document in documents:
        frontmatter = document.frontmatter
        if frontmatter.status != LifecycleStatus.ACTIVE:
            continue
        risk_flags = tuple(frontmatter.risk_flags)
        risk_flags = (*risk_flags, *scan_text(document.body, field="memory").risk_flags)
        if has_unsafe_recall_risk(risk_flags):
            continue
        if profile_type == "project":
            if frontmatter.project != project:
                continue
        elif frontmatter.project is not None or frontmatter.scope not in {
            MemoryScope.USER,
            MemoryScope.GLOBAL,
        }:
            continue
        selected.append(document)

    return tuple(
        _candidate_from_document(config, document)
        for document in sorted(selected, key=lambda document: _document_sort_key(config, document))
    )


def _fit_candidates(
    candidates: Sequence[ProfileCandidate],
    *,
    profile_type: str,
    project: Optional[str],
    generated_at: datetime,
    budget: int,
) -> tuple[tuple[ProfileCandidate, ...], bool]:
    selected: list[ProfileCandidate] = []
    base_markdown = render_profile_markdown(
        profile_type=profile_type,
        project=project,
        generated_at=generated_at,
        token_budget=budget,
        items=(),
    )
    if estimate_tokens(base_markdown) > budget:
        raise ValueError("budget is too small to render required profile frontmatter")

    truncated = False
    for candidate in candidates:
        trial = [*selected, candidate]
        trial_markdown = render_profile_markdown(
            profile_type=profile_type,
            project=project,
            generated_at=generated_at,
            token_budget=budget,
            items=_items_from_candidates(trial),
        )
        if estimate_tokens(trial_markdown) <= budget:
            selected = trial
        else:
            truncated = True
    return tuple(selected), truncated


def _items_from_candidates(candidates: Sequence[ProfileCandidate]) -> tuple[ProfileItem, ...]:
    return tuple(
        ProfileItem(
            memory_id=candidate.memory_id,
            memory_type=candidate.memory_type,
            summary=candidate.summary,
            citation_key=f"C{position}",
            relative_path=candidate.relative_path,
        )
        for position, candidate in enumerate(candidates, start=1)
    )


def _candidate_from_document(config: MemoryConfig, document: MemoryDocument) -> ProfileCandidate:
    frontmatter = document.frontmatter
    return ProfileCandidate(
        memory_id=frontmatter.id,
        memory_type=frontmatter.type.value,
        summary=_summary_text(document),
        relative_path=_relative_path(config, document),
    )


def _document_sort_key(config: MemoryConfig, document: MemoryDocument) -> tuple[int, str, str]:
    frontmatter = document.frontmatter
    memory_type = frontmatter.type.value
    return (
        TYPE_ORDER.index(memory_type) if memory_type in TYPE_ORDER else len(TYPE_ORDER),
        frontmatter.id,
        _relative_path(config, document).as_posix(),
    )


def _summary_text(document: MemoryDocument, *, max_words: int = 24) -> str:
    for observation in document.frontmatter.observations:
        text = _clean_summary_text(observation.text)
        if text:
            return _truncate_words(text, max_words=max_words)
    body_text = _clean_summary_text(document.body)
    if body_text:
        return _truncate_words(body_text, max_words=max_words)
    return document.frontmatter.id


def _clean_summary_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned_lines.append(re.sub(r"^[-*]\s+", "", stripped))
    return re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()


def _truncate_words(text: str, *, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(".,;:") + "..."


def _ordered_types(items: Sequence[ProfileItem]) -> tuple[str, ...]:
    present = {item.memory_type for item in items}
    ordered = [memory_type for memory_type in TYPE_ORDER if memory_type in present]
    ordered.extend(sorted(present - set(TYPE_ORDER)))
    return tuple(ordered)


def _relative_path(config: MemoryConfig, document: MemoryDocument) -> Path:
    if document.path is None:
        return Path(document.frontmatter.id)
    try:
        return document.path.relative_to(config.vault_path)
    except ValueError:
        return document.path


def _profile_title(*, profile_type: str, project: Optional[str]) -> str:
    if profile_type == "project":
        return f"{project} Profile"
    return "User Profile"


def _profile_alias(*, profile_type: str, project: Optional[str]) -> str:
    if profile_type == "project":
        return f"Memora Project Profile: {project}"
    return "Memora User Profile"


def _title_from_type(memory_type: str) -> str:
    return memory_type.replace("_", " ").title()


def _validate_profile_type(value: str) -> str:
    selected = str(value).strip().lower()
    if selected not in PROFILE_TYPES:
        raise ValueError("profile_type must be one of: user, project")
    return selected


def _validate_budget(value: int) -> int:
    budget = int(value)
    if budget < 1:
        raise ValueError("budget must be at least 1")
    return budget


def _configured_budget(config: MemoryConfig, profile_type: str) -> int:
    if profile_type == "project":
        return config.profile.project_budget
    return config.profile.user_budget


def _context_profile_budget(
    config: MemoryConfig,
    *,
    profile_type: str,
    task_budget: Optional[int],
) -> int:
    configured = _configured_budget(config, profile_type)
    if task_budget is None:
        return configured
    return max(1, min(configured, int(task_budget)))


def _normalize_now(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.tzinfo.utcoffset(current) is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _namespace_profile_citations(
    markdown: str,
    citations: Sequence[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    key_map: dict[str, str] = {}
    namespaced_citations: list[dict[str, Any]] = []
    for position, citation in enumerate(citations, start=1):
        original_key = str(citation.get("key") or f"C{position}")
        namespaced_key = f"{PROFILE_INJECTION_CITATION_PREFIX}{position}"
        key_map[original_key] = namespaced_key
        updated = dict(citation)
        updated["key"] = namespaced_key
        namespaced_citations.append(updated)

    namespaced_markdown = markdown
    for original_key, namespaced_key in key_map.items():
        namespaced_markdown = namespaced_markdown.replace(
            f"[{original_key}]", f"[{namespaced_key}]"
        )
    return namespaced_markdown, namespaced_citations


__all__ = [
    "PROFILE_INJECTION_CITATION_PREFIX",
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_TYPES",
    "ProfileCandidate",
    "ProfileItem",
    "ProfileResult",
    "build_context_profile_payload",
    "generate_profile_context",
    "render_profile_markdown",
]
