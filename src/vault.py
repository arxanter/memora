"""Shared vault operations used by the CLI."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from config import MemoryConfig, create_default_config, load_config, write_config
from markdown import aliases as presentation_aliases
from markdown import readable_title, wikilink_for_memory, wikilink_for_path
from schema import (
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
from safety import merge_scan_results, normalize_risk_flags, scan_text
from sync import atomic_write_text, detect_sync_conflicts, vault_lock

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
class SetupResult:
    vault_path: Path
    config_path: Path
    dry_run: bool
    actions: tuple[dict[str, Any], ...]
    created_paths: tuple[Path, ...] = ()
    config_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        would_write = any(bool(action.get("would_write")) for action in self.actions)
        return {
            "ok": True,
            "implemented": True,
            "command": "setup",
            "dry_run": self.dry_run,
            "vault_path": str(self.vault_path),
            "config_path": str(self.config_path),
            "would_write": would_write,
            "config_created": self.config_created,
            "created_paths": [str(path) for path in self.created_paths],
            "actions": list(self.actions),
            "next_steps": [
                "Run without --dry-run to create the planned vault files.",
                "Run `memora agent rules --client agents` to generate coding-agent instructions.",
                "Run `memora agent integrate --client cursor --project <path> --dry-run` to preview project rule installation.",
            ]
            if self.dry_run
            else [
                "Run `memora doctor --vault <vault>` to validate the vault.",
                "Run `memora agent rules --client agents` to generate coding-agent instructions.",
                "Run `memora agent integrate --client cursor --project <path>` to connect a project agent.",
            ],
        }


@dataclass(frozen=True)
class RememberResult:
    memory_id: str
    path: Path
    relative_path: Path
    status: LifecycleStatus
    memory_type: MemoryType
    risk_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "id": self.memory_id,
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "type": self.memory_type.value,
            "status": self.status.value,
            "risk_flags": list(self.risk_flags),
        }


def init_vault(vault_path: Union[Path, str]) -> InitResult:
    """Create the vault layout and default config without overwriting data."""

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


def setup_vault(vault_path: Union[Path, str], *, dry_run: bool = False) -> SetupResult:
    """Plan or create the default vault layout without overwriting user files."""

    config = create_default_config(vault_path)
    planned_paths = (*_vault_directories(config), config.config_path)
    exists_before = {path: path.exists() for path in planned_paths}

    created_paths: tuple[Path, ...] = ()
    config_created = False
    if not dry_run:
        result = init_vault(config.vault_path)
        created_paths = result.created_paths
        config_created = result.config_created

    actions = _setup_actions(
        config,
        exists_before=exists_before,
        created_paths=set(created_paths),
        dry_run=dry_run,
    )
    return SetupResult(
        vault_path=config.vault_path,
        config_path=config.config_path,
        dry_run=dry_run,
        actions=actions,
        created_paths=created_paths,
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
    risk_flags: Iterable[str] = (),
) -> RememberResult:
    """Create one canonical Markdown memory file."""

    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("memory text must not be empty")
    selected_tags = tuple(tags)

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
    safety = merge_scan_results(
        scan_text(cleaned_text, field="memory"),
        scan_text(" ".join(selected_tags), field="tags"),
    )
    selected_risk_flags = normalize_risk_flags((*risk_flags, *safety.risk_flags))
    if (
        selected_author_kind == AuthorKind.AGENT
        and selected_status == LifecycleStatus.ACTIVE
        and selected_risk_flags
    ):
        selected_status = LifecycleStatus.PENDING
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
        tags=list(selected_tags),
        risk_flags=list(selected_risk_flags),
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
        risk_flags=selected_risk_flags,
    )


def render_memory_markdown(frontmatter: MemoryFrontmatter, body: str) -> str:
    """Render a validated memory as Obsidian-compatible Markdown."""

    frontmatter_data = _memory_frontmatter_with_presentation(frontmatter, body)
    rendered_yaml = yaml.safe_dump(frontmatter_data, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_yaml}\n---\n\n{body.strip()}\n"


def _memory_frontmatter_with_presentation(
    frontmatter: MemoryFrontmatter, body: str
) -> dict[str, Any]:
    """Add optional Obsidian presentation fields without changing canonical ids."""

    frontmatter_data = frontmatter.model_dump(mode="json", exclude_none=False)
    title = frontmatter.title or readable_title(body, fallback=frontmatter.id)
    frontmatter_data["title"] = title
    frontmatter_data["aliases"] = frontmatter.aliases or presentation_aliases(title, frontmatter.id)

    source_links = list(frontmatter.source_links)
    if frontmatter.source and frontmatter.source.path and not source_links:
        source_links = [
            wikilink_for_path(
                frontmatter.source.path,
                label=frontmatter.source.title or "Source",
            )
        ]
    frontmatter_data["source_links"] = source_links

    relation_links = list(frontmatter.relation_links)
    if not relation_links:
        relation_links = _memory_relation_links(frontmatter)
    frontmatter_data["relation_links"] = relation_links

    for field in ("aliases", "source_links", "relation_links"):
        if not frontmatter_data[field]:
            frontmatter_data.pop(field)
    return frontmatter_data


def _memory_relation_links(frontmatter: MemoryFrontmatter) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    def add_link(relation: str, target: str) -> None:
        link = f"{relation}: {wikilink_for_memory(target)}"
        if link in seen:
            return
        seen.add(link)
        links.append(link)

    for relation in frontmatter.relations:
        add_link(relation.type.value, relation.target)
    for target in frontmatter.supersedes:
        add_link(RelationType.SUPERSEDES.value, target)
    for target in frontmatter.contradicts:
        add_link(RelationType.CONTRADICTS.value, target)
    return links


def status_summary(config: MemoryConfig) -> dict[str, Any]:
    """Return a lightweight vault status summary."""

    report = validate_vault(config.vault_path)
    pending_count = sum(
        1 for document in report.documents if document.frontmatter.status == LifecycleStatus.PENDING
    )
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
        from indexer import validate_graph

        graph_report = validate_graph(config)
        graph_payload = graph_report.to_dict()
        graph_issues = [
            {
                "kind": "graph",
                **issue.to_dict(),
            }
            for issue in graph_report.issues
        ]

    contradiction_warnings = (
        [] if schema_issues else _contradiction_warnings(report.documents, config)
    )
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
    memory_dirs = tuple(
        config.memory_root / directory for directory in MEMORY_TYPE_DIRECTORIES.values()
    )
    return (
        config.vault_path,
        config.raw_root,
        config.raw_root / "inbox",
        config.raw_root / "inbox" / "webclips",
        config.raw_root / "inbox" / "files",
        config.raw_root / "inbox" / "sessions",
        config.raw_root / "inbox" / "slack",
        config.raw_root / "inbox" / "zoom",
        config.raw_root / "inbox" / "failed",
        config.raw_root / "processed",
        config.raw_root / "quarantine",
        config.memory_root,
        *memory_dirs,
        config.vault_path / config.sources_dir,
        config.vault_path / config.briefs_dir,
        config.vault_path / config.memora_dir,
        config.vault_path / config.memora_dir / "schemas",
        config.vault_path / config.memora_dir / "cache",
        config.vault_path / config.memora_dir / "embeddings",
        config.vault_path / config.memora_dir / "locks",
    )


def _setup_actions(
    config: MemoryConfig,
    *,
    exists_before: dict[Path, bool],
    created_paths: set[Path],
    dry_run: bool,
) -> tuple[dict[str, Any], ...]:
    actions: list[dict[str, Any]] = []
    for path in _vault_directories(config):
        existed = exists_before.get(path, path.exists())
        actions.append(
            _setup_action(
                config,
                path,
                kind="directory",
                action="create_directory",
                existed=existed,
                created=path in created_paths,
                dry_run=dry_run,
            )
        )
    config_existed = exists_before.get(config.config_path, config.config_path.exists())
    actions.append(
        _setup_action(
            config,
            config.config_path,
            kind="config",
            action="write_config",
            existed=config_existed,
            created=config.config_path in created_paths,
            dry_run=dry_run,
        )
    )
    return tuple(actions)


def _setup_action(
    config: MemoryConfig,
    path: Path,
    *,
    kind: str,
    action: str,
    existed: bool,
    created: bool,
    dry_run: bool,
) -> dict[str, Any]:
    would_write = not existed and dry_run
    status = "exists" if existed else "planned" if dry_run else "created" if created else "skipped"
    return {
        "kind": kind,
        "action": action,
        "path": str(path),
        "relative_path": _setup_relative_path(config, path),
        "exists": existed,
        "would_write": would_write,
        "created": created,
        "status": status,
    }


def _setup_relative_path(config: MemoryConfig, path: Path) -> str:
    try:
        return path.relative_to(config.vault_path).as_posix() or "."
    except ValueError:
        return str(path)


def _new_memory_id(now: datetime) -> str:
    return f"mem_{now:%Y%m%d}_{secrets.token_hex(3)}"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48].strip("-") or "memory"
