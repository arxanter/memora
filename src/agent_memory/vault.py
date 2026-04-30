"""Shared vault operations used by the CLI and future MCP tools."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from agent_memory.config import MemoryConfig, create_default_config, load_config, write_config
from agent_memory.schema import (
    AuthorKind,
    AuthorMetadata,
    LifecycleStatus,
    MemoryFrontmatter,
    MemoryScope,
    MemoryType,
    Observation,
    RelationType,
    SCHEMA_VERSION,
    SourceRef,
    parse_markdown_document,
    validate_vault,
)
from agent_memory.sync import atomic_write_text, detect_sync_conflicts, vault_lock

MEMORY_TYPE_DIRECTORIES: dict[MemoryType, str] = {
    MemoryType.FACT: "facts",
    MemoryType.PREFERENCE: "preferences",
    MemoryType.DECISION: "decisions",
    MemoryType.TASK: "tasks",
    MemoryType.SOURCE_EXTRACT: "sources",
    MemoryType.PROJECT_CONTEXT: "projects",
    MemoryType.CONVERSATION_SUMMARY: "conversations",
}


@dataclass(frozen=True)
class InitResult:
    vault_path: Path
    config_path: Path
    created_paths: tuple[Path, ...]
    config_created: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vault_path": str(self.vault_path),
            "config_path": str(self.config_path),
            "config_created": self.config_created,
            "created_paths": [str(path) for path in self.created_paths],
        }


@dataclass(frozen=True)
class RememberResult:
    memory_id: str
    path: Path
    relative_path: Path
    status: LifecycleStatus
    memory_type: MemoryType

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "id": self.memory_id,
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "type": self.memory_type.value,
            "status": self.status.value,
        }


def init_vault(vault_path: Union[Path, str]) -> InitResult:
    """Create the Stage 2 vault layout and default config without overwriting data."""

    config = create_default_config(vault_path)
    created_paths: list[Path] = []

    for path in _vault_directories(config):
        if not path.exists():
            created_paths.append(path)
        path.mkdir(parents=True, exist_ok=True)

    config_created = write_config(config, overwrite=False)
    if config_created:
        created_paths.append(config.config_path)
    else:
        config = load_config(config.vault_path)

    return InitResult(
        vault_path=config.vault_path,
        config_path=config.config_path,
        created_paths=tuple(created_paths),
        config_created=config_created,
    )


def remember_memory(
    config: MemoryConfig,
    *,
    memory_type: MemoryType,
    text: str,
    scope: Optional[MemoryScope] = None,
    project: Optional[str] = None,
    status: Optional[LifecycleStatus] = None,
    tags: Iterable[str] = (),
    author_kind: AuthorKind = AuthorKind.USER,
    author_name: Optional[str] = None,
    source: Optional[Union[SourceRef, dict[str, Any]]] = None,
    confidence: Optional[float] = None,
) -> RememberResult:
    """Create one canonical Markdown memory file."""

    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("memory text must not be empty")

    selected_scope = MemoryScope(scope or config.default_scope)
    selected_project = project or config.default_project
    if selected_scope == MemoryScope.PROJECT and not selected_project:
        raise ValueError("project-scoped memory requires --project or default_project in config")

    selected_author_kind = AuthorKind(author_kind)
    selected_status = LifecycleStatus(
        status
        or (
            config.agent_default_status
            if selected_author_kind == AuthorKind.AGENT
            else config.user_default_status
        )
    )
    selected_source = (
        source
        if isinstance(source, SourceRef) or source is None
        else SourceRef.model_validate(source)
    )
    now = datetime.now(timezone.utc).astimezone()
    memory_id = _new_memory_id(now)
    frontmatter = MemoryFrontmatter(
        schema_version=SCHEMA_VERSION,
        id=memory_id,
        type=memory_type,
        scope=selected_scope,
        project=selected_project,
        status=selected_status,
        created_at=now,
        updated_at=now,
        valid_from=now.date(),
        valid_to=None,
        confidence=confidence,
        source=selected_source,
        author=AuthorMetadata(
            kind=selected_author_kind,
            name=author_name or config.default_author_name,
        ),
        supersedes=[],
        contradicts=[],
        relations=[],
        observations=[
            Observation(
                category=memory_type.value,
                text=cleaned_text,
                confidence=confidence,
            )
        ],
        tags=list(tags),
    )

    markdown = render_memory_markdown(frontmatter, cleaned_text)
    parse_markdown_document(markdown)

    with vault_lock(config):
        target_dir = config.memory_root / MEMORY_TYPE_DIRECTORIES[memory_type]
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{memory_id}-{_slugify(cleaned_text)}.md"
        atomic_write_text(target_path, markdown)

    return RememberResult(
        memory_id=memory_id,
        path=target_path,
        relative_path=target_path.relative_to(config.vault_path),
        status=selected_status,
        memory_type=memory_type,
    )


def render_memory_markdown(frontmatter: MemoryFrontmatter, body: str) -> str:
    """Render a validated memory as Obsidian-compatible Markdown."""

    frontmatter_data = frontmatter.model_dump(mode="json", exclude_none=False)
    rendered_yaml = yaml.safe_dump(frontmatter_data, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_yaml}\n---\n\n{body.strip()}\n"


def status_summary(config: MemoryConfig) -> dict[str, Any]:
    """Return a lightweight vault status summary."""

    report = validate_vault(config.vault_path)
    pending_count = sum(1 for document in report.documents if document.frontmatter.status == LifecycleStatus.PENDING)
    return {
        "ok": report.ok,
        "vault_path": str(config.vault_path),
        "config_path": str(config.config_path),
        "memory_count": len(report.documents),
        "pending_count": pending_count,
        "issue_count": len(report.issues),
        "index_exists": config.index_file.exists(),
        "index_path": str(config.index_file),
        "stage": "indexer",
    }


def doctor_report(config: MemoryConfig) -> dict[str, Any]:
    """Validate vault schema and durable graph references."""

    report = validate_vault(config.vault_path)
    conflict_report = detect_sync_conflicts(config)
    schema_issues = [
        {
            "kind": "schema",
            "path": str(issue.path),
            "message": issue.message,
        }
        for issue in report.issues
    ]
    graph_payload = {
        "ok": not schema_issues,
        "documents": len(report.documents),
        "links": 0,
        "orphan_count": 0,
        "issues": [],
    }
    graph_issues: list[dict[str, Any]] = []
    if not schema_issues:
        from agent_memory.indexer import validate_graph

        graph_report = validate_graph(config)
        graph_payload = graph_report.to_dict()
        graph_issues = [
            {
                "kind": "graph",
                **issue.to_dict(),
            }
            for issue in graph_report.issues
        ]

    contradiction_warnings = [] if schema_issues else _contradiction_warnings(report.documents, config)
    sync_issues = []
    for issue in conflict_report.conflicts:
        if issue.kind == "invalid_frontmatter":
            continue
        payload = issue.to_dict(config.vault_path)
        payload["kind"] = f"sync:{issue.kind}"
        sync_issues.append(payload)
    issues = [*schema_issues, *graph_issues, *sync_issues]
    return {
        "ok": not issues,
        "vault_path": str(config.vault_path),
        "documents": len(report.documents),
        "graph": graph_payload,
        "conflicts": conflict_report.to_dict(),
        "conflict_count": conflict_report.to_dict()["conflict_count"],
        "warnings": contradiction_warnings,
        "warning_count": len(contradiction_warnings),
        "contradiction_count": len(contradiction_warnings),
        "issues": issues,
    }


def placeholder_result(command: str, **details: Any) -> dict[str, Any]:
    """Structured Stage 2 placeholder for later retrieval/indexing services."""

    return {
        "ok": True,
        "implemented": False,
        "command": command,
        "message": f"{command} is a Stage 2 CLI placeholder; implementation is planned for later stages.",
        **details,
    }


def _contradiction_warnings(documents: Iterable[Any], config: MemoryConfig) -> list[dict[str, Any]]:
    known_paths = {
        document.frontmatter.id: (
            document.path.relative_to(config.vault_path).as_posix()
            if document.path and document.path.is_absolute()
            else str(document.path or "")
        )
        for document in documents
    }
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for document in documents:
        frontmatter = document.frontmatter
        targets = list(frontmatter.contradicts)
        targets.extend(
            relation.target
            for relation in frontmatter.relations
            if relation.type == RelationType.CONTRADICTS
        )
        for target in targets:
            signature = (frontmatter.id, target)
            if signature in seen:
                continue
            seen.add(signature)
            warnings.append(
                {
                    "kind": "contradiction",
                    "path": known_paths.get(frontmatter.id, ""),
                    "from_id": frontmatter.id,
                    "to_id": target,
                    "relation": RelationType.CONTRADICTS.value,
                    "message": f"contradiction recorded: {frontmatter.id} contradicts {target}",
                }
            )
    return warnings


def _vault_directories(config: MemoryConfig) -> tuple[Path, ...]:
    memory_dirs = tuple(config.memory_root / directory for directory in MEMORY_TYPE_DIRECTORIES.values())
    return (
        config.vault_path,
        config.memory_root,
        *memory_dirs,
        config.vault_path / config.sources_dir,
        config.vault_path / config.briefs_dir,
        config.vault_path / config.profiles_dir,
        config.vault_path / config.profiles_dir / "projects",
        config.vault_path / config.synthesis_dir,
        config.vault_path / config.agent_memory_dir,
        config.vault_path / config.agent_memory_dir / "schemas",
        config.vault_path / config.agent_memory_dir / "cache",
        config.vault_path / config.agent_memory_dir / "embeddings",
        config.vault_path / config.agent_memory_dir / "locks",
    )


def _new_memory_id(now: datetime) -> str:
    return f"mem_{now:%Y%m%d}_{secrets.token_hex(3)}"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48].strip("-") or "memory"
