"""Shared helpers for installing Memora instructions into LLM agents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

from config import AgentPolicyConfig, ConfigError, load_config


class AgentClient(str, Enum):
    """Agent clients with generated Memora instructions."""

    AGENTS = "agents"
    CURSOR = "cursor"
    CLAUDE = "claude"
    CODEX = "codex"


class IntegrationScope(str, Enum):
    """Where generated agent integration material is installed."""

    PROJECT = "project"
    USER = "user"


class TargetSupport(str, Enum):
    """How confident Memora is about an integration target path."""

    SUPPORTED = "supported"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class IntegrationTarget:
    client: AgentClient
    scope: IntegrationScope
    path: Path
    support: TargetSupport
    reason: str
    explicit: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "client": self.client.value,
            "scope": self.scope.value,
            "path": str(self.path),
            "support": self.support.value,
            "reason": self.reason,
            "explicit": self.explicit,
        }


SUPPORTED_AGENT_RULE_FORMATS = frozenset(client.value for client in AgentClient)
SUPPORTED_AGENT_RULE_CLIENTS = SUPPORTED_AGENT_RULE_FORMATS
DEFAULT_AGENT_INTEGRATION_CLIENTS = (AgentClient.CURSOR, AgentClient.CLAUDE, AgentClient.CODEX)
SCHEDULED_TEMPLATE_KINDS = frozenset({"email", "calendar", "slack", "web", "custom"})

AGENT_RULES_TEMPLATE_VERSION = "agent-rules-v2"
MANAGED_BLOCK_BEGIN = "<!-- BEGIN AGENT MEMORY MANAGED BLOCK -->"
MANAGED_BLOCK_END = "<!-- END AGENT MEMORY MANAGED BLOCK -->"
_TEMPLATE_VERSION_RE = re.compile(r"template_version:\s*([^\s<>]+)")
_CONTENT_HASH_RE = re.compile(r"content_hash:\s*(sha256:[0-9a-f]{64})")
_SCHEDULED_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CURSOR_RULE_FRONTMATTER = "\n".join(
    [
        "---",
        "description: Use Memora CLI for durable project memory",
        "alwaysApply: true",
        "---",
    ]
)


def resolve_rule_aliases(
    *,
    vault_path: Optional[Path] = None,
    alias_overrides: Optional[Sequence[str]] = None,
) -> list[str]:
    """Resolve assistant name aliases for generated rules (vault config or overrides)."""

    if alias_overrides:
        return AgentPolicyConfig(aliases=list(alias_overrides)).aliases
    try:
        if vault_path is not None:
            return load_config(vault_path).agent_policy.aliases
        return load_config(start_path=Path.cwd()).agent_policy.aliases
    except ConfigError:
        return AgentPolicyConfig().aliases


def _cyrillic_aliases(aliases: Sequence[str]) -> list[str]:
    return [a for a in aliases if re.search(r"[\u0400-\u04ff]", a)]


def _primary_latin_alias(aliases: Sequence[str]) -> str:
    for alias in aliases:
        if not re.search(r"[\u0400-\u04ff]", alias):
            return alias
    return aliases[0]


def _intent_routing_lines(aliases: Sequence[str]) -> list[str]:
    primary = _primary_latin_alias(aliases)
    cyrillic = _cyrillic_aliases(aliases)
    rows = [
        (
            f"{primary}, show current facts about <topic>",
            "покажи текущие факты по <topic>",
            "run `memora probe --intent memory` with likely `--variant` forms first, then expand only relevant candidates.",
        ),
        (
            f"{primary}, show what the wiki knows about <topic>",
            "покажи wiki по <topic>",
            "run `memora context --intent wiki` or `memora wiki search`, then expand only the needed pages.",
        ),
        (
            f"{primary}, what did we decide about <topic>",
            "что мы решили по <topic>",
            "run `memora probe --intent memory` with likely `--variant` forms first; use `build-context` only when you need a packed cited brief.",
        ),
        (
            f"{primary}, save this fact/decision/preference",
            "сохрани это как факт/решение/preference",
            "create one atomic memory with `memora remember`; lifecycle follows `agent_policy`.",
        ),
        (
            f"{primary}, review pending memory",
            "проверь pending memory",
            "run `memora review`, present a compact queue, and ask before approve/reject unless policy allows autonomous action.",
        ),
        (
            f"{primary}, update memory for <topic>",
            "актуализируй память по <topic>",
            "search related active/pending items, propose supersede/reject/defer/new memory, and ask before lifecycle changes unless policy allows autonomous action.",
        ),
        (
            f"{primary}, analyze this source and save it",
            "проанализируй источник и сохрани",
            "stage raw material when needed, create an extract, save curated source evidence, promote only durable atomic items, and update Wiki when useful.",
        ),
    ]
    lines: list[str] = []
    for english_phrase, russian_tail, instruction in rows:
        parts = [f"`{english_phrase}`"]
        for name in cyrillic:
            parts.append(f"`{name}, {russian_tail}`")
        lines.append("- " + " / ".join(parts) + f": {instruction}")
    return lines


def agent_group_rules_payload(
    *,
    client: str,
    scope: str,
    vault: Optional[Path],
    project: Optional[str],
    alias_overrides: Optional[list[str]] = None,
) -> dict[str, object]:
    selected_client = _normalize_client(client, kind="client")
    selected_scope = _coerce_scope(scope)
    selected_project = project.strip() if project else None
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    aliases = resolve_rule_aliases(vault_path=resolved_vault, alias_overrides=alias_overrides)
    content = render_agent_rules(
        selected_client,
        vault_path=resolved_vault,
        project=selected_project,
        agent_aliases=aliases,
    )
    return {
        "ok": True,
        "implemented": True,
        "command": "agent rules",
        "client": selected_client.value,
        "scope": selected_scope.value,
        "vault_path": str(resolved_vault) if resolved_vault else None,
        "project": selected_project,
        "agent_aliases": aliases,
        "alias_overrides": alias_overrides,
        "content": content,
    }


def agent_targets_payload(
    *,
    client: str,
    scope: str,
    project: Path,
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    clients = select_agent_clients(client)
    targets = [
        resolve_integration_target(
            selected_client,
            scope=selected_scope,
            project_path=project_path,
        ).to_dict()
        for selected_client in clients
    ]
    return {
        "ok": True,
        "implemented": True,
        "command": "agent targets",
        "client": client.strip().lower(),
        "scope": selected_scope.value,
        "project_path": str(project_path),
        "targets": targets,
    }


def agent_integrate_payload(
    *,
    client: str,
    scope: str,
    project: Path,
    target: Optional[Path],
    vault: Optional[Path],
    dry_run: bool,
    force: bool,
    alias_overrides: Optional[list[str]] = None,
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    clients = select_agent_clients(client)
    if target is not None and len(clients) != 1:
        raise ValueError("--target can only be used when one client is selected")

    aliases = resolve_rule_aliases(vault_path=resolved_vault, alias_overrides=alias_overrides)
    results = [
        apply_agent_integration(
            client=selected_client,
            scope=selected_scope,
            project_path=project_path,
            target=target,
            vault_path=resolved_vault,
            dry_run=dry_run,
            force=force,
            command="agent integrate",
            agent_aliases=aliases,
        )
        for selected_client in clients
    ]
    return _agent_operation_payload(
        command="agent integrate",
        selected_client=client,
        scope=selected_scope,
        project_path=project_path,
        vault_path=resolved_vault,
        dry_run=dry_run,
        force=force,
        results=results,
    )


def agent_update_payload(
    *,
    client: str,
    scope: str,
    project: Path,
    target: Optional[Path],
    vault: Optional[Path],
    dry_run: bool,
    force: bool,
    alias_overrides: Optional[list[str]] = None,
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    clients = select_agent_clients(client)
    if target is not None and len(clients) != 1:
        raise ValueError("--target can only be used when one client is selected")

    aliases = resolve_rule_aliases(vault_path=resolved_vault, alias_overrides=alias_overrides)
    results = [
        apply_agent_integration(
            client=selected_client,
            scope=selected_scope,
            project_path=project_path,
            target=target,
            vault_path=resolved_vault,
            dry_run=dry_run,
            force=force,
            command="agent update",
            agent_aliases=aliases,
        )
        for selected_client in clients
    ]
    return _agent_operation_payload(
        command="agent update",
        selected_client=client,
        scope=selected_scope,
        project_path=project_path,
        vault_path=resolved_vault,
        dry_run=dry_run,
        force=force,
        results=results,
    )


def agent_status_payload(
    *,
    client: str,
    scope: str,
    project: Path,
    vault: Optional[Path] = None,
    alias_overrides: Optional[list[str]] = None,
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    aliases = resolve_rule_aliases(vault_path=resolved_vault, alias_overrides=alias_overrides)
    results = [
        agent_target_status(
            client=selected_client,
            scope=selected_scope,
            project_path=project_path,
            vault_path=resolved_vault,
            agent_aliases=aliases,
        )
        for selected_client in select_agent_clients(client)
    ]
    return {
        "ok": True,
        "implemented": True,
        "command": "agent status",
        "client": client.strip().lower(),
        "scope": selected_scope.value,
        "project_path": str(project_path),
        "vault_path": str(resolved_vault) if resolved_vault else None,
        "results": results,
        "installed_count": sum(1 for result in results if result["installed"]),
        "missing_count": sum(1 for result in results if result["status"] == "missing"),
        "needs_update_count": sum(1 for result in results if result["needs_update"]),
        "manual_count": sum(1 for result in results if result["status"] == "manual"),
    }


def agent_doctor_payload(
    *,
    client: str,
    scope: str,
    project: Path,
    vault: Optional[Path],
    memora_command_path: Optional[str],
    vault_probe: dict[str, Any],
    alias_overrides: Optional[list[str]] = None,
) -> dict[str, object]:
    status = agent_status_payload(
        client=client,
        scope=scope,
        project=project,
        vault=vault,
        alias_overrides=alias_overrides,
    )
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if memora_command_path is None:
        warnings.append(
            {
                "code": "memora_command_not_found",
                "message": "`memora` was not found on PATH for external agent use.",
            }
        )
    if not vault_probe.get("ok"):
        item = {
            "code": "vault_status_failed",
            "message": str(vault_probe.get("message") or "vault status could not be loaded"),
        }
        if vault_probe.get("explicit"):
            issues.append(item)
        else:
            warnings.append(
                {
                    "code": "vault_not_discoverable",
                    "message": item["message"],
                }
            )
    return {
        "ok": not issues,
        "implemented": True,
        "command": "agent doctor",
        "client": client.strip().lower(),
        "scope": status["scope"],
        "project_path": status["project_path"],
        "vault_path": status["vault_path"],
        "memora_command": {
            "available": memora_command_path is not None,
            "path": memora_command_path,
        },
        "vault_status": vault_probe,
        "target_status": status["results"],
        "issues": issues,
        "warnings": warnings,
        "issue_count": len(issues),
        "warning_count": len(warnings),
    }


def agent_scheduled_template_payload(
    *,
    client: str,
    kind: str,
    project: Optional[str],
) -> dict[str, object]:
    selected_client = _normalize_client(client, kind="client")
    selected_kind = normalize_scheduled_kind(kind)
    selected_project = project.strip() if project else "<project>"
    channel = scheduled_source_channel(selected_kind)
    kind_hint = _scheduled_kind_hint(selected_kind)
    steps = [
        "Confirm run frequency, source boundaries, allowed accounts/workspaces, sensitivity, and project before the first run.",
        "Fetch or read only the requested source through the scheduled agent's normal client tools; Memora does not fetch email, Slack, calendars, or web pages by itself.",
        "Stage the fetched raw file with `memora raw add` so the run has traceable input metadata.",
        "Create one concise extract for the run with Summary, Durable Facts, Decisions, Tasks, Preferences, Open Questions, and Relevant Quotes.",
        "Preserve curated evidence with `memora source add`.",
        "Move each successfully processed raw input out of the inbox with `memora raw mark-processed`.",
        "Promote only durable atomic facts, decisions, preferences, tasks, or project context with `memora remember`.",
        "Update the maintained Wiki layer with `memora wiki ingest <source_id>` when the source should enrich topic/entity/concept pages.",
        "Return a compact report with inspected source count, saved source id/path, pending memory count, rejected proposal count, and review command.",
    ]
    safety = [
        "Do not store secrets, credentials, auth tokens, private dumps, raw mailbox exports, or sensitive personal data as canonical memory.",
        "Preserve raw/source material and a concise extract under Sources/; do not promote unprocessed dumps into Memories/.",
        "Use Wiki/ for maintained overviews, entities, concepts, and saved syntheses; use Memories/ only for atomic operational memory.",
        "Promote only small durable atomic memories that will be useful later.",
        "Leave inferred or agent-authored memories pending unless vault policy explicitly allows activation.",
        "Keep scheduled workflows bounded; do not turn Memora into an email, Slack, calendar, or web daemon.",
    ]
    template = "\n".join(
        [
            "# Scheduled Memora Task",
            "",
            f"Client: {selected_client.value}",
            f"Source kind: {selected_kind}",
            f"Source channel: {channel}",
            f"Project: {selected_project}",
            "",
            "Run frequency: <daily/weekly/monthly or explicit cron/schedule>",
            f"Source to inspect: <{kind_hint}>",
            "Source boundaries: <date range, labels/channels/calendars/sites, inclusion/exclusion rules>",
            "Allowed accounts/workspaces: <account, workspace, tenant, or profile names>",
            "Sensitivity: <normal|private|secret|unsafe>",
            "",
            "Agent steps:",
            *[f"{index}. {step}" for index, step in enumerate(steps, start=1)],
            "",
            "Core ingest commands:",
            "```bash",
            f'memora raw add <raw-file> --kind {selected_kind if selected_kind in {"slack"} else "text"} --format markdown',
            f'memora source add <source.md> --extract <extract.md> --kind {selected_kind if selected_kind in {"slack"} else "text"}',
            "memora raw mark-processed <raw-file> --source-id <source_id>",
            f'memora remember --type fact --project "{selected_project}" --text "<durable atomic fact>"',
            "memora wiki ingest <source_id> --entity <Entity> --concept <Concept>",
            "```",
            "",
            "Safety guidance:",
            *[f"- {item}" for item in safety],
        ]
    )
    return {
        "ok": True,
        "implemented": True,
        "command": "agent scheduled-template",
        "client": selected_client.value,
        "kind": selected_kind,
        "project": selected_project,
        "template": template,
        "content": template,
        "steps": steps,
        "safety": safety,
    }


def agent_session_template_payload(
    *,
    client: str,
    project: Optional[str],
) -> dict[str, object]:
    selected_client = _normalize_client(client, kind="client")
    selected_project = project.strip() if project else "<project>"
    content = "\n".join(
        [
            "# Memora Session-End Capture",
            "",
            f"Client: {selected_client.value}",
            f"Project: {selected_project}",
            "",
            "At the end of a substantial task:",
            "1. Create a concise session summary with decisions, durable facts, tasks, and open questions.",
            "2. If a transcript export is available, save it with `memora session finalize --summary-file <summary>`.",
            "3. Propose only durable atomic memories; avoid raw logs, secrets, and transient implementation chatter.",
            "4. Save durable research answers or broad summaries as Wiki syntheses only when explicitly requested.",
            "5. Leave inferred memories pending unless policy explicitly allows activation.",
            "6. Report the source saved, pending memory count, Wiki synthesis/page updates, and review command.",
        ]
    )
    return {
        "ok": True,
        "implemented": True,
        "command": "agent session-template",
        "client": selected_client.value,
        "project": selected_project,
        "content": content,
    }


def select_agent_clients(client: str) -> tuple[AgentClient, ...]:
    selected_client = client.strip().lower()
    if selected_client not in {"all", *SUPPORTED_AGENT_RULE_CLIENTS}:
        raise ValueError("client must be one of: all, agents, cursor, claude, codex")
    if selected_client == "all":
        return DEFAULT_AGENT_INTEGRATION_CLIENTS
    return (_normalize_client(selected_client, kind="client"),)


def apply_agent_integration(
    *,
    client: AgentClient | str,
    scope: IntegrationScope | str,
    project_path: Path,
    target: Optional[Path],
    vault_path: Optional[Path],
    dry_run: bool,
    force: bool,
    command: str,
    agent_aliases: Optional[Sequence[str]] = None,
) -> dict[str, object]:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    aliases = (
        list(agent_aliases)
        if agent_aliases is not None
        else resolve_rule_aliases(vault_path=vault_path)
    )
    rendered = render_agent_rules(
        selected_client,
        vault_path=vault_path,
        project=project_path.name,
        agent_aliases=aliases,
    )
    target_info = resolve_integration_target(
        selected_client,
        scope=selected_scope,
        project_path=project_path,
        target=target,
    )
    plan = plan_managed_agent_write(
        target_info=target_info,
        content=rendered,
        dry_run=dry_run,
        force=force,
    )
    written = False
    if not dry_run and plan["would_write"]:
        target_info.path.parent.mkdir(parents=True, exist_ok=True)
        target_info.path.write_text(str(plan["planned_content"]), encoding="utf-8")
        written = True
    return _agent_result_payload(
        command=command,
        target_info=target_info,
        plan=plan,
        dry_run=dry_run,
        force=force,
        written=written,
    )


def plan_managed_agent_write(
    *,
    target_info: IntegrationTarget,
    content: str,
    dry_run: bool,
    force: bool,
) -> dict[str, object]:
    target_exists = target_info.path.exists()
    managed_payload = managed_agent_payload(target_info.client, content)
    new_hash = managed_content_hash(managed_payload)
    new_block = render_managed_block(managed_payload)
    new_file_content = render_managed_agent_file(target_info.client, content)
    existing_content = target_info.path.read_text(encoding="utf-8") if target_exists else ""
    metadata = managed_block_metadata(existing_content) if target_exists else {}
    managed = bool(metadata)
    existing_hash = (
        metadata.get("content_hash")
        if managed
        else (file_content_hash(target_info.path) if target_exists else None)
    )
    detected_version = metadata.get("template_version")
    needs_update = existing_hash != new_hash
    needs_manual_merge = False

    if not target_exists:
        action = "create"
        planned_content = new_file_content
        would_write = True
    elif managed:
        action = "update_managed_block" if needs_update else "noop"
        planned_content = (
            replace_managed_block(existing_content, new_block) if needs_update else existing_content
        )
        would_write = needs_update
    elif force:
        action = "overwrite"
        planned_content = new_file_content
        would_write = True
    else:
        action = "append_managed_block"
        planned_content = append_managed_block(existing_content, new_block)
        would_write = True

    return {
        "action": action,
        "target_exists": target_exists,
        "managed": managed,
        "legacy_migratable": False,
        "target_hash": existing_hash,
        "content_hash": new_hash,
        "detected_version": detected_version,
        "template_version": AGENT_RULES_TEMPLATE_VERSION,
        "needs_update": needs_update,
        "needs_manual_merge": needs_manual_merge,
        "blocked": action == "blocked",
        "would_write": would_write,
        "dry_run": dry_run,
        "force": force,
        "planned_content": planned_content,
    }


def agent_target_status(
    *,
    client: AgentClient | str,
    scope: IntegrationScope | str,
    project_path: Path,
    vault_path: Optional[Path],
    agent_aliases: Optional[Sequence[str]] = None,
) -> dict[str, object]:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    target_info = resolve_integration_target(
        selected_client,
        scope=selected_scope,
        project_path=project_path,
    )
    aliases = (
        list(agent_aliases)
        if agent_aliases is not None
        else resolve_rule_aliases(vault_path=vault_path)
    )
    content = render_agent_rules(
        selected_client,
        vault_path=vault_path,
        project=project_path.name,
        agent_aliases=aliases,
    )
    expected_hash = managed_content_hash(managed_agent_payload(selected_client, content))
    target_exists = target_info.path.exists()
    metadata: dict[str, str] = {}
    target_hash: Optional[str] = None
    managed = False
    if target_exists:
        existing = target_info.path.read_text(encoding="utf-8")
        metadata = managed_block_metadata(existing)
        managed = bool(metadata)
        target_hash = (
            metadata.get("content_hash") if managed else file_content_hash(target_info.path)
        )

    if not target_exists:
        status = "missing"
        needs_update = True
    elif managed:
        needs_update = target_hash != expected_hash
        status = "outdated" if needs_update else "installed"
    else:
        needs_update = True
        status = "appendable"

    return {
        "client": selected_client.value,
        "scope": selected_scope.value,
        "target": target_info.to_dict(),
        "target_path": str(target_info.path),
        "support": target_info.support.value,
        "installed": target_exists,
        "status": status,
        "managed": managed,
        "legacy_migratable": False,
        "target_hash": target_hash,
        "content_hash": expected_hash,
        "detected_version": metadata.get("template_version"),
        "template_version": AGENT_RULES_TEMPLATE_VERSION,
        "needs_update": needs_update,
        "needs_manual_merge": False,
    }


def replace_managed_block(existing_content: str, new_block: str) -> str:
    start = existing_content.index(MANAGED_BLOCK_BEGIN)
    end = existing_content.index(MANAGED_BLOCK_END, start) + len(MANAGED_BLOCK_END)
    before = existing_content[:start].rstrip()
    after = existing_content[end:].lstrip("\n")
    parts = []
    if before:
        parts.append(before)
    parts.append(new_block.rstrip())
    if after:
        parts.append(after.rstrip())
    return "\n\n".join(parts) + "\n"


def append_managed_block(existing_content: str, new_block: str) -> str:
    parts = [existing_content.rstrip(), new_block.rstrip()]
    return "\n\n".join(part for part in parts if part) + "\n"


def _agent_operation_payload(
    *,
    command: str,
    selected_client: str,
    scope: IntegrationScope,
    project_path: Path,
    vault_path: Optional[Path],
    dry_run: bool,
    force: bool,
    results: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "ok": True,
        "implemented": True,
        "command": command,
        "client": selected_client.strip().lower(),
        "scope": scope.value,
        "project_path": str(project_path),
        "vault_path": str(vault_path) if vault_path is not None else None,
        "dry_run": dry_run,
        "force": force,
        "results": results,
        "blocked_count": sum(1 for result in results if result["blocked"]),
        "written_count": sum(1 for result in results if result["written"]),
        "would_write_count": sum(1 for result in results if result["would_write"]),
    }


def _agent_result_payload(
    *,
    command: str,
    target_info: IntegrationTarget,
    plan: dict[str, object],
    dry_run: bool,
    force: bool,
    written: bool,
) -> dict[str, object]:
    return {
        "command": command,
        "client": target_info.client.value,
        "scope": target_info.scope.value,
        "target": target_info.to_dict(),
        "target_path": str(target_info.path),
        "target_exists": plan["target_exists"],
        "managed": plan["managed"],
        "legacy_migratable": plan["legacy_migratable"],
        "action": plan["action"],
        "blocked": plan["blocked"],
        "needs_manual_merge": plan["needs_manual_merge"],
        "would_write": plan["would_write"],
        "written": written,
        "dry_run": dry_run,
        "force": force,
        "needs_update": plan["needs_update"],
        "target_hash": plan["target_hash"],
        "content_hash": plan["content_hash"],
        "detected_version": plan["detected_version"],
        "template_version": plan["template_version"],
    }


def resolve_integration_target(
    client: AgentClient | str,
    *,
    scope: IntegrationScope | str = IntegrationScope.PROJECT,
    project_path: Optional[Path] = None,
    target: Optional[Path] = None,
    home: Optional[Path] = None,
) -> IntegrationTarget:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    base_project_path = (project_path or Path(".")).expanduser().resolve()
    if target is not None:
        candidate = target.expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (base_project_path / candidate).resolve()
        )
        return IntegrationTarget(
            client=selected_client,
            scope=selected_scope,
            path=resolved,
            support=TargetSupport.SUPPORTED,
            reason="explicit target path",
            explicit=True,
        )
    if selected_scope == IntegrationScope.PROJECT:
        return _project_target(selected_client, base_project_path)
    return _user_target(selected_client, home=home)


def render_agent_rules(
    format_name: AgentClient | str,
    *,
    vault_path: Optional[Path],
    project: Optional[str],
    agent_aliases: Optional[Sequence[str]] = None,
) -> str:
    selected_format = _coerce_client(format_name)
    aliases = (
        list(agent_aliases)
        if agent_aliases is not None
        else resolve_rule_aliases(vault_path=vault_path)
    )
    vault_arg = f' --vault "{vault_path}"' if vault_path else ""
    project_arg = f' --project "{project}"' if project else ' --project "<project-name>"'
    lines = agent_rules_body(vault_arg=vault_arg, project_arg=project_arg, aliases=aliases)
    if selected_format == AgentClient.CURSOR:
        return "\n".join(
            [
                _CURSOR_RULE_FRONTMATTER,
                "",
                *lines,
            ]
        )
    if selected_format == AgentClient.CLAUDE:
        return "\n".join(["# Memora Instructions", "", *lines])
    if selected_format == AgentClient.CODEX:
        return "\n".join(["# Memora Instructions For Codex", "", *lines])
    return "\n".join(["## Memora Usage", "", *lines])


def agent_rules_body(*, vault_arg: str, project_arg: str, aliases: Sequence[str]) -> list[str]:
    probe = f'memora probe "<query>"{vault_arg}{project_arg} --intent auto --variant "<alternate wording>" --variant "<synonym or translated form>"'
    build_context = f'memora build-context "<task>"{vault_arg}{project_arg} --task-class planning'
    unscoped_probe = f'memora probe "<query>"{vault_arg} --intent auto --variant "<alternate wording>" --variant "<synonym or translated form>"'
    unscoped_build_context = f'memora build-context "<task>"{vault_arg} --task-class planning'
    unscoped_search = f'memora search "<query>"{vault_arg}'
    context = f'memora context "<query>"{vault_arg}{project_arg} --intent auto --budget 1200'
    wiki_search = f'memora wiki search "<topic>"{vault_arg}'
    wiki_read = f"memora wiki read <page>{vault_arg}"
    wiki_ingest = f"memora wiki ingest <source_id>{vault_arg}"
    wiki_synthesize = f'memora wiki synthesize "<question>"{vault_arg} --save'
    brief = f'memora brief "<topic>"{vault_arg}{project_arg}'
    search = f'memora search "<query>"{vault_arg}{project_arg}'
    review = f"memora review{vault_arg}"
    remember = f'memora remember{vault_arg}{project_arg} --type decision --text "<durable decision>"'
    raw_add = (
        f"memora raw add <raw-file>{vault_arg}{project_arg} --kind text --format markdown"
    )
    source_add = f"memora source add <source.md>{vault_arg}{project_arg} --extract <extract.md> --kind text"
    raw_processed = f"memora raw mark-processed <raw-file>{vault_arg} --source-id <source_id>"
    session_finalize = f"memora session finalize <transcript>{vault_arg}{project_arg} --summary-file <summary.md> --memories-file <memories.json>"
    primary = _primary_latin_alias(aliases)
    addressing = "/".join(aliases)
    routing_lines = _intent_routing_lines(aliases)
    return [
        "Current product direction is CLI-first and CLI-only for agents. Use `memora ...` commands from any project directory for recall, search, source lookup, raw staging, curated source evidence, Wiki maintenance, memory writes, review, status, indexing, and session capture.",
        "",
        "Prefer the default compact agent output and inspect individual memories on demand with `memora inspect <id>`.",
        "",
        "Do not read, write, edit, delete, or migrate Memora vault files directly. This includes `vault/Memories/`, `vault/Sources/`, `vault/Wiki/`, `vault/raw/`, `state/index.sqlite`, cache, embeddings, locks, and schema files. Treat vault paths, SQLite/cache internals, frontmatter, filenames, and generated schema as private storage managed by the CLI.",
        "",
        "If the CLI lacks an operation, stop and report the missing command or product gap. Do not bypass the CLI with direct file edits, SQL, migrations, cache manipulation, or ad hoc scripts.",
        "",
        "For a compact command and option reference, use `docs/cli-agent-reference.md` when it is available in the project; otherwise run `memora help` for the current public command surface.",
        "",
        f"Do not run memora recall for every turn. Use memory when the request addresses {addressing}, asks for current facts, decisions, preferences, earlier work, project history/status, or asks to save/analyze durable knowledge.",
        "",
        "When memory lookup is relevant, use `memora probe` as the first discovery call. If you can confidently classify the request, pass the explicit probe intent (`--intent memory`, `--intent wiki`, or `--intent mixed`); use `--intent auto` only when unsure. Generate 2-5 likely alternate query constructions and pass them with repeated `--variant`: synonyms, translated RU/EN wording, important inflections/cases, abbreviations, and domain terms. Keep variants concise and high-signal; do not issue separate `context`, `search`, and `build-context` calls for the same discovery step.",
        "",
        "Choose recall/search scope deliberately:",
        "",
        '- Use the project filter for questions about this repository, the current product, local implementation details, project decisions, current branch/status, TODOs, roadmap, bugs, tests, CLI behavior, or anything phrased as "in this project".',
        "- Omit the project filter for general questions about durable user preferences, cross-project conventions, agent behavior, recurring personal/work context, or prior conversations that are not clearly tied to the current project.",
        "- If the request mixes project and general context, start with the narrower project scope when the work is in this repository; run an unscoped lookup only when the answer still needs user-wide history or preferences.",
        "- If scope is unclear, infer from the task target: code/workspace/change requests are project-scoped; preference/history/identity questions are usually unscoped.",
        "",
        "For project-scoped discovery, start with:",
        "",
        "```bash",
        probe,
        "```",
        "",
        "Use `build-context` after discovery only when you need Memora to pack a cited brief under a task budget:",
        "",
        "```bash",
        build_context,
        "```",
        "",
        "For unscoped recall/search, omit the project filter:",
        "",
        "```bash",
        unscoped_probe,
        unscoped_build_context,
        unscoped_search,
        "```",
        "",
        "For `probe`, treat `has_context=true` as the signal to inspect or expand candidates; `memory_needed` only means the memory surface has candidates. `probe` searches only `Memories/` and `Wiki/`, never `Sources/`. For `build-context`, use returned context only when `memory_needed=true`. Preserve citations when answering or making decisions from recalled context. If `probe` finds compact candidates, prefer the printed expansion commands (`memora inspect` or `memora wiki read`) over broad follow-up searches.",
        "",
        "Use `memora context` when the request may need flexible routing across Memories, Wiki, and Sources without overloading the agent context:",
        "",
        "```bash",
        context,
        "```",
        "",
        "Context routing rules:",
        "",
        "- Current decisions, preferences, tasks, project status, and facts that should affect agent behavior belong in `Memories/` and should be discovered with `memora probe --intent memory`; use `memora build-context` only when a packed cited brief is needed.",
        "- Topic overviews, entity/concept pages, comparisons, and saved research answers belong in `Wiki/` and should be retrieved with `memora context --intent wiki` or `memora wiki search`.",
        "- Provenance, quotations, article text, transcripts, and evidence belong in `Sources/` and should be retrieved with `memora context --intent evidence` or `memora lookup-source`.",
        "- Ambiguous research/planning questions should start with `memora context --intent mixed`; expand individual candidates with the printed `inspect`, `wiki read`, or `lookup-source` command instead of loading everything. Use `context`, not `probe`, when saved source evidence is required.",
        "- If `Wiki/` conflicts with active `Memories/`, treat the Wiki page as stale and update it through the CLI; do not silently overwrite active memories.",
        "",
        f"{primary} intent routing examples:",
        "",
        *routing_lines,
        "",
        f"Useful {primary} commands:",
        "",
        "```bash",
        brief,
        search,
        wiki_search,
        wiki_read,
        remember,
        "```",
        "",
        "During a session, notice memory-worthy information and offer to save it when it is durable and likely useful later:",
        "",
        "- Propose saving explicit user preferences, recurring workflow preferences, project decisions, stable constraints, roadmap/status updates, unresolved tasks, important bug findings, or agreed implementation direction.",
        "- Do not propose saving transient implementation chatter, temporary logs, speculative ideas, secrets, raw dumps, sensitive personal data, or facts already obvious from the current code.",
        '- Keep prompts lightweight: "This seems useful to remember as <type>. Save it?" Only write after explicit approval unless the user directly asks to remember/save it.',
        "",
        "Source capture workflow: the AI agent reads or fetches the material first, stages unprocessed input in `raw/`, writes a concise extract, preserves curated evidence in `Sources/`, moves the processed raw file to `raw/processed` with `memora raw mark-processed`, then promotes only durable atomic facts, decisions, preferences, project context, or tasks. If the source should enrich maintained knowledge, update `Wiki/` through `memora wiki ingest` or save a durable answer through `memora wiki synthesize --save`.",
        "",
        "```bash",
        raw_add,
        source_add,
        raw_processed,
        remember,
        wiki_ingest,
        wiki_synthesize,
        "```",
        "",
        "Do not store secrets, raw dumps, temporary logs, or unreviewed summaries as canonical memory. Canonical memories should be small, durable, cited when possible, and reviewable. `memora brief` is ephemeral agent output; durable briefs and analyses should be saved as `Wiki/syntheses/` through the CLI.",
        "",
        "Review and lifecycle workflow: agent-created or inferred memories should stay reviewable according to `config.yaml` policy. Review pending items with:",
        "",
        "```bash",
        review,
        "```",
        "",
        "Present id, type, confidence, source, risk flags, summary, and recommended action. Do not approve or reject without explicit confirmation unless the vault policy allows autonomous lifecycle changes with source, confidence, reason, and audit history.",
        "",
        "Session-end capture workflow: produce one concise summary of decisions, durable facts, tasks, and open questions. If a transcript/export is available, finalize it through the CLI with proposed memories:",
        "",
        "```bash",
        session_finalize,
        "```",
        "",
        "Chat-noise reduction: do not narrate every `memora ...` call or paste large JSON. Summarize final effects only: source saved, pending memories created, review required, no durable memory found, or CLI gap encountered.",
        "",
        "Scheduled task guidance: confirm source boundaries if ambiguous; fetch only requested sources; stage raw input with `memora raw add`; preserve curated evidence with `memora source add`; move processed raw files with `memora raw mark-processed`; never persist secrets, credentials, auth tokens, private personal data, or raw mailbox dumps as canonical memory; create one extract per run; promote only durable atomic items; return source count, pending memory count, and review command.",
        "",
    ]


def managed_content_hash(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def managed_agent_payload(client: AgentClient | str, content: str) -> str:
    """Return the generated payload covered by managed block metadata."""

    selected_client = _coerce_client(client)
    if selected_client == AgentClient.CURSOR:
        _, body = split_cursor_rule_frontmatter(content)
        return body
    return content


def render_managed_agent_file(client: AgentClient | str, content: str) -> str:
    """Render generated instructions as an installable managed target file."""

    selected_client = _coerce_client(client)
    payload = managed_agent_payload(selected_client, content)
    managed_block = render_managed_block(payload)
    if selected_client != AgentClient.CURSOR:
        return managed_block
    frontmatter, _ = split_cursor_rule_frontmatter(content)
    return "\n\n".join([frontmatter, managed_block.rstrip()]) + "\n"


def split_cursor_rule_frontmatter(content: str) -> tuple[str, str]:
    lines = content.splitlines()
    if lines and lines[0] == "---":
        for index in range(1, len(lines)):
            if lines[index] == "---":
                frontmatter = "\n".join(lines[: index + 1])
                body = "\n".join(lines[index + 1 :]).lstrip("\n")
                return frontmatter, body
    return _CURSOR_RULE_FRONTMATTER, content.lstrip("\n")


def file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def render_managed_block(
    content: str,
    *,
    template_version: str = AGENT_RULES_TEMPLATE_VERSION,
) -> str:
    content_hash = managed_content_hash(content)
    return "\n".join(
        [
            MANAGED_BLOCK_BEGIN,
            f"<!-- template_version: {template_version} -->",
            f"<!-- content_hash: {content_hash} -->",
            content,
            MANAGED_BLOCK_END,
            "",
        ]
    )


def managed_block_metadata(content: str) -> dict[str, str]:
    if MANAGED_BLOCK_BEGIN not in content or MANAGED_BLOCK_END not in content:
        return {}
    start = content.index(MANAGED_BLOCK_BEGIN)
    end = content.index(MANAGED_BLOCK_END, start) + len(MANAGED_BLOCK_END)
    block = content[start:end]
    metadata: dict[str, str] = {}
    template_version = _TEMPLATE_VERSION_RE.search(block)
    content_hash = _CONTENT_HASH_RE.search(block)
    if template_version:
        metadata["template_version"] = template_version.group(1)
    if content_hash:
        metadata["content_hash"] = content_hash.group(1)
    return metadata


def normalize_scheduled_kind(kind: str) -> str:
    """Return a compact, deterministic scheduled source kind slug."""

    selected = re.sub(r"[^a-z0-9_-]+", "_", (kind or "custom").strip().lower()).strip("_-")
    if not selected:
        selected = "custom"
    if not _SCHEDULED_KIND_RE.fullmatch(selected):
        raise ValueError(
            "kind must start with a letter and contain only letters, numbers, underscores, or hyphens"
        )
    return selected


def scheduled_source_channel(kind: str) -> str:
    """Return the source channel recorded for scheduled source captures."""

    return f"scheduled_{normalize_scheduled_kind(kind).replace('-', '_')}"


def _scheduled_kind_hint(kind: str) -> str:
    if kind == "email":
        return "mailbox folders/labels, senders, subjects, and date window"
    if kind == "calendar":
        return "calendar names, event types, attendees, and date window"
    if kind == "slack":
        return "workspace, channels, threads, users, and date window"
    if kind == "web":
        return "allowed URLs/domains, pages, feeds, and date window"
    return "explicit exported source files and boundaries"


def _project_target(client: AgentClient, project_path: Path) -> IntegrationTarget:
    if client == AgentClient.CURSOR:
        path = project_path / ".cursor" / "rules" / "memora.mdc"
        reason = "Cursor project rules file"
    elif client == AgentClient.CLAUDE:
        path = project_path / "CLAUDE.md"
        reason = "Claude project instructions file"
    else:
        path = project_path / "AGENTS.md"
        reason = "AGENTS.md project instructions file"
    return IntegrationTarget(
        client=client,
        scope=IntegrationScope.PROJECT,
        path=path,
        support=TargetSupport.SUPPORTED,
        reason=reason,
    )


def _user_target(client: AgentClient, *, home: Optional[Path]) -> IntegrationTarget:
    home_path = (home or Path.home()).expanduser().resolve()
    integration_root = home_path / ".memora"
    if client == AgentClient.CURSOR:
        path = integration_root / "cursor-memora.mdc"
        reason = "safe fallback file for manual Cursor user-level installation"
        support = TargetSupport.FALLBACK
    elif client == AgentClient.CLAUDE:
        path = home_path / ".claude" / "CLAUDE.md"
        reason = "Claude Code user memory file"
        support = TargetSupport.SUPPORTED
    elif client == AgentClient.CODEX:
        path = home_path / ".codex" / "AGENTS.md"
        reason = "Codex global AGENTS.md file"
        support = TargetSupport.SUPPORTED
    else:
        path = integration_root / "AGENTS.md"
        reason = "safe fallback AGENTS.md for manual user-level installation"
        support = TargetSupport.FALLBACK
    return IntegrationTarget(
        client=client,
        scope=IntegrationScope.USER,
        path=path,
        support=support,
        reason=reason,
    )


def _normalize_client(value: str, *, kind: str) -> AgentClient:
    selected = value.strip().lower()
    try:
        return AgentClient(selected)
    except ValueError as exc:
        if kind == "format":
            raise ValueError("format must be one of: agents, cursor, claude, codex") from exc
        raise ValueError("client must be one of: agents, cursor, claude, codex") from exc


def _coerce_client(value: AgentClient | str) -> AgentClient:
    if isinstance(value, AgentClient):
        return value
    return _normalize_client(value, kind="client")


def _coerce_scope(value: IntegrationScope | str) -> IntegrationScope:
    if isinstance(value, IntegrationScope):
        return value
    try:
        return IntegrationScope(value.strip().lower())
    except ValueError as exc:
        raise ValueError("scope must be one of: project, user") from exc
