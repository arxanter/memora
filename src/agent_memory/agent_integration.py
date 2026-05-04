"""Shared helpers for installing Agent Memory instructions into LLM agents."""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class AgentClient(str, Enum):
    """Agent clients with generated Agent Memory instructions."""

    AGENTS = "agents"
    CURSOR = "cursor"
    CLAUDE = "claude"
    CODEX = "codex"


class IntegrationScope(str, Enum):
    """Where generated agent integration material is installed."""

    PROJECT = "project"
    USER = "user"


class TargetSupport(str, Enum):
    """How confident Agent Memory is about an integration target path."""

    SUPPORTED = "supported"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


class IntegrationAction(str, Enum):
    """Planned file action for an integration target."""

    CREATE = "create"
    OVERWRITE = "overwrite"
    BLOCKED = "blocked"
    DRY_RUN = "dry_run"


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


@dataclass(frozen=True)
class IntegrationPlan:
    target: IntegrationTarget
    content: str
    action: IntegrationAction
    target_exists: bool
    existing_hash: Optional[str]
    new_hash: str
    would_write: bool
    needs_update: bool
    force: bool
    dry_run: bool
    detected_version: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.action == IntegrationAction.BLOCKED


@dataclass(frozen=True)
class IntegrationResult:
    plan: IntegrationPlan
    written: bool


SUPPORTED_AGENT_RULE_FORMATS = frozenset(client.value for client in AgentClient)
SUPPORTED_AGENT_RULE_CLIENTS = SUPPORTED_AGENT_RULE_FORMATS
SUPPORTED_AGENT_INSTALL_COMMAND_CLIENTS = frozenset({"all", *SUPPORTED_AGENT_RULE_CLIENTS})
DEFAULT_INSTALL_COMMAND_CLIENTS = (AgentClient.CURSOR, AgentClient.CLAUDE, AgentClient.CODEX)
SCHEDULED_TEMPLATE_KINDS = frozenset({"email", "calendar", "slack", "web", "custom"})

AGENT_RULES_TEMPLATE_VERSION = "agent-rules-v2"
MANAGED_BLOCK_BEGIN = "<!-- BEGIN AGENT MEMORY MANAGED BLOCK -->"
MANAGED_BLOCK_END = "<!-- END AGENT MEMORY MANAGED BLOCK -->"
_TEMPLATE_VERSION_RE = re.compile(r"template_version:\s*([^\s<>]+)")
_CONTENT_HASH_RE = re.compile(r"content_hash:\s*(sha256:[0-9a-f]{64})")
_SCHEDULED_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def agent_rules_payload(
    *,
    rule_format: str,
    vault: Optional[Path],
    project: Optional[str],
) -> dict[str, object]:
    selected_format = _normalize_client(rule_format, kind="format")
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    selected_project = project.strip() if project else None
    content = render_agent_rules(
        selected_format,
        vault_path=resolved_vault,
        project=selected_project,
    )
    return {
        "ok": True,
        "implemented": True,
        "command": "agent-rules",
        "format": selected_format.value,
        "vault_path": str(resolved_vault) if resolved_vault else None,
        "project": selected_project,
        "content": content,
    }


def install_agent_rules_payload(
    *,
    client: str,
    project: Path,
    target: Optional[Path],
    vault: Optional[Path],
    dry_run: bool,
    force: bool,
) -> dict[str, object]:
    selected_client = _normalize_client(client, kind="client")
    project_path = project.expanduser().resolve()
    content = render_agent_rules(
        selected_client,
        vault_path=vault.expanduser().resolve() if vault is not None else None,
        project=project_path.name,
    )
    plan = plan_integration(
        client=selected_client,
        scope=IntegrationScope.PROJECT,
        project_path=project_path,
        target=target,
        content=content,
        dry_run=dry_run,
        force=force,
    )

    payload = {
        "ok": True,
        "implemented": True,
        "command": "install-agent-rules",
        "client": selected_client.value,
        "project_path": str(project_path),
        "target_path": str(plan.target.path),
        "target_exists": plan.target_exists,
        "dry_run": dry_run,
        "force": force,
        "blocked": plan.blocked,
        "would_write": plan.would_write,
        "written": False,
        "content": content,
    }

    if dry_run:
        return payload
    if plan.blocked:
        raise ValueError(f"target exists: {plan.target.path}; pass --force to overwrite")

    plan.target.path.parent.mkdir(parents=True, exist_ok=True)
    plan.target.path.write_text(content, encoding="utf-8")
    payload["written"] = True
    return payload


def agent_install_commands_payload(
    *,
    project: Path,
    client: str,
    vault: Optional[Path],
    force: bool,
    dry_run_first: bool,
) -> dict[str, object]:
    selected_client = client.strip().lower()
    clients = agent_install_command_clients(selected_client)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    commands = [
        install_command_plan(
            client=install_client,
            project_path=project_path,
            vault_path=resolved_vault,
            force=force,
            dry_run_first=dry_run_first,
        )
        for install_client in clients
    ]
    return {
        "ok": True,
        "implemented": True,
        "command": "agent-install-commands",
        "client": selected_client,
        "project_path": str(project_path),
        "vault_path": str(resolved_vault) if resolved_vault is not None else None,
        "force": force,
        "dry_run_first": dry_run_first,
        "commands": commands,
    }


def agent_group_rules_payload(
    *,
    client: str,
    scope: str,
    vault: Optional[Path],
    project: Optional[str],
) -> dict[str, object]:
    selected_client = _normalize_client(client, kind="client")
    selected_scope = _coerce_scope(scope)
    selected_project = project.strip() if project else None
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    content = render_agent_rules(
        selected_client,
        vault_path=resolved_vault,
        project=selected_project,
    )
    return {
        "ok": True,
        "implemented": True,
        "command": "agent rules",
        "client": selected_client.value,
        "scope": selected_scope.value,
        "vault_path": str(resolved_vault) if resolved_vault else None,
        "project": selected_project,
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
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    clients = select_agent_clients(client)
    if target is not None and len(clients) != 1:
        raise ValueError("--target can only be used when one client is selected")

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
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    clients = select_agent_clients(client)
    if target is not None and len(clients) != 1:
        raise ValueError("--target can only be used when one client is selected")

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
) -> dict[str, object]:
    selected_scope = _coerce_scope(scope)
    project_path = project.expanduser().resolve()
    resolved_vault = vault.expanduser().resolve() if vault is not None else None
    results = [
        agent_target_status(
            client=selected_client,
            scope=selected_scope,
            project_path=project_path,
            vault_path=resolved_vault,
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
    memory_command_path: Optional[str],
    vault_probe: dict[str, Any],
) -> dict[str, object]:
    status = agent_status_payload(client=client, scope=scope, project=project, vault=vault)
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if memory_command_path is None:
        warnings.append(
            {
                "code": "memory_command_not_found",
                "message": "`memory` was not found on PATH for external agent use.",
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
        "memory_command": {
            "available": memory_command_path is not None,
            "path": memory_command_path,
        },
        "vault_status": vault_probe,
        "target_status": status["results"],
        "issues": issues,
        "warnings": warnings,
        "issue_count": len(issues),
        "warning_count": len(warnings),
    }


def agent_group_commands_payload(
    *,
    project: Path,
    client: str,
    vault: Optional[Path],
    force: bool,
    dry_run_first: bool,
) -> dict[str, object]:
    payload = agent_install_commands_payload(
        project=project,
        client=client,
        vault=vault,
        force=force,
        dry_run_first=dry_run_first,
    )
    return {
        **payload,
        "command": "agent commands",
        "compatibility_command": "agent-install-commands",
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
        "Fetch or read only the requested source through the scheduled agent's normal client tools; Agent Memory does not fetch email, Slack, calendars, or web pages by itself.",
        "Create one concise extract for the run with Summary, Durable Facts, Decisions, Tasks, Preferences, Open Questions, and Relevant Quotes.",
        "Preserve the prepared source and extract with `memory scheduled ingest` or another explicit source import command.",
        "Promote only durable atomic facts, decisions, preferences, tasks, or project context as pending memories.",
        "Return a compact report with inspected source count, saved source id/path, pending memory count, rejected proposal count, and review command.",
    ]
    safety = [
        "Do not store secrets, credentials, auth tokens, private dumps, raw mailbox exports, or sensitive personal data as canonical memory.",
        "Preserve raw/source material and a concise extract under Sources/; do not promote unprocessed dumps into Memories/.",
        "Promote only small durable atomic memories that will be useful later.",
        "Leave inferred or agent-authored memories pending unless vault policy explicitly allows activation.",
        "Keep scheduled workflows bounded; do not turn Agent Memory into an email, Slack, calendar, or web daemon.",
    ]
    template = "\n".join(
        [
            "# Scheduled Agent Memory Task",
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
            "Safe ingest command:",
            "```bash",
            f'memory scheduled ingest --kind {selected_kind} --project "{selected_project}" --source-file <source.md> --extract-file <extract.md> --memories-file <memories.json> --json',
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
            "# Agent Memory Session-End Capture",
            "",
            f"Client: {selected_client.value}",
            f"Project: {selected_project}",
            "",
            "At the end of a substantial task:",
            "1. Create a concise session summary with decisions, durable facts, tasks, and open questions.",
            "2. If a transcript export is available, save it with `memory import-session --summary-file <summary> --json`.",
            "3. Propose only durable atomic memories; avoid raw logs, secrets, and transient implementation chatter.",
            "4. Leave inferred memories pending unless policy explicitly allows activation.",
            "5. Report the source saved, pending memory count, and review command.",
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


def agent_install_command_clients(client: str) -> tuple[AgentClient, ...]:
    selected_client = client.strip().lower()
    if selected_client not in SUPPORTED_AGENT_INSTALL_COMMAND_CLIENTS:
        raise ValueError("client must be one of: all, agents, cursor, claude, codex")
    if selected_client == "all":
        return DEFAULT_INSTALL_COMMAND_CLIENTS
    return (_normalize_client(selected_client, kind="client"),)


def select_agent_clients(client: str) -> tuple[AgentClient, ...]:
    return agent_install_command_clients(client)


def install_command_plan(
    *,
    client: AgentClient | str,
    project_path: Path,
    vault_path: Optional[Path],
    force: bool,
    dry_run_first: bool,
) -> dict[str, Optional[str]]:
    selected_client = _coerce_client(client)
    target = resolve_integration_target(
        selected_client,
        scope=IntegrationScope.PROJECT,
        project_path=project_path,
    )
    base_args = [
        "memory",
        "install-agent-rules",
        "--client",
        selected_client.value,
        "--project",
        str(project_path),
    ]
    if vault_path is not None:
        base_args.extend(["--vault", str(vault_path)])
    if force:
        base_args.append("--force")
    install_command = shell_command(base_args)
    dry_run_command = shell_command([*base_args, "--dry-run"]) if dry_run_first else None
    return {
        "client": selected_client.value,
        "target_path": str(target.path),
        "dry_run_command": dry_run_command,
        "install_command": install_command,
    }


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
) -> dict[str, object]:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    rendered = render_agent_rules(
        selected_client,
        vault_path=vault_path,
        project=project_path.name,
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
    new_hash = managed_content_hash(content)
    new_block = render_managed_block(content)
    existing_content = target_info.path.read_text(encoding="utf-8") if target_exists else ""
    metadata = managed_block_metadata(existing_content) if target_exists else {}
    managed = bool(metadata)
    existing_hash = metadata.get("content_hash") if managed else (
        file_content_hash(target_info.path) if target_exists else None
    )
    detected_version = metadata.get("template_version")
    needs_update = existing_hash != new_hash
    needs_manual_merge = target_exists and not managed and not force

    if not target_exists:
        action = "create"
        planned_content = new_block
        would_write = True
    elif needs_manual_merge:
        action = "blocked"
        planned_content = existing_content
        would_write = False
    elif managed:
        action = "update_managed_block" if needs_update else "noop"
        planned_content = replace_managed_block(existing_content, new_block) if needs_update else existing_content
        would_write = needs_update
    else:
        action = "overwrite"
        planned_content = new_block
        would_write = True

    return {
        "action": action,
        "target_exists": target_exists,
        "managed": managed,
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
) -> dict[str, object]:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    target_info = resolve_integration_target(
        selected_client,
        scope=selected_scope,
        project_path=project_path,
    )
    content = render_agent_rules(
        selected_client,
        vault_path=vault_path,
        project=project_path.name,
    )
    expected_hash = managed_content_hash(content)
    target_exists = target_info.path.exists()
    metadata: dict[str, str] = {}
    target_hash: Optional[str] = None
    managed = False
    if target_exists:
        existing = target_info.path.read_text(encoding="utf-8")
        metadata = managed_block_metadata(existing)
        managed = bool(metadata)
        target_hash = metadata.get("content_hash") if managed else file_content_hash(target_info.path)

    if not target_exists:
        status = "missing"
        needs_update = True
    elif managed:
        needs_update = target_hash != expected_hash
        status = "outdated" if needs_update else "installed"
    else:
        needs_update = target_hash != expected_hash
        status = "manual" if needs_update else "installed_unmanaged"

    return {
        "client": selected_client.value,
        "scope": selected_scope.value,
        "target": target_info.to_dict(),
        "target_path": str(target_info.path),
        "support": target_info.support.value,
        "installed": target_exists,
        "status": status,
        "managed": managed,
        "target_hash": target_hash,
        "content_hash": expected_hash,
        "detected_version": metadata.get("template_version"),
        "template_version": AGENT_RULES_TEMPLATE_VERSION,
        "needs_update": needs_update,
        "needs_manual_merge": status == "manual",
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


def plan_integration(
    *,
    client: AgentClient | str,
    scope: IntegrationScope | str,
    project_path: Path,
    target: Optional[Path],
    content: str,
    dry_run: bool,
    force: bool,
) -> IntegrationPlan:
    selected_client = _coerce_client(client)
    selected_scope = _coerce_scope(scope)
    integration_target = resolve_integration_target(
        selected_client,
        scope=selected_scope,
        project_path=project_path,
        target=target,
    )
    target_exists = integration_target.path.exists()
    existing_hash = file_content_hash(integration_target.path) if target_exists else None
    new_hash = managed_content_hash(content)
    detected_version = managed_block_metadata(
        integration_target.path.read_text(encoding="utf-8")
    ).get("template_version") if target_exists else None
    blocked = target_exists and not force
    if blocked:
        action = IntegrationAction.BLOCKED
    elif dry_run:
        action = IntegrationAction.DRY_RUN
    elif target_exists:
        action = IntegrationAction.OVERWRITE
    else:
        action = IntegrationAction.CREATE
    return IntegrationPlan(
        target=integration_target,
        content=content,
        action=action,
        target_exists=target_exists,
        existing_hash=existing_hash,
        new_hash=new_hash,
        would_write=not blocked,
        needs_update=existing_hash != new_hash,
        force=force,
        dry_run=dry_run,
        detected_version=detected_version,
    )


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
        resolved = candidate.resolve() if candidate.is_absolute() else (base_project_path / candidate).resolve()
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
) -> str:
    selected_format = _coerce_client(format_name)
    vault_arg = f' --vault "{vault_path}"' if vault_path else ""
    project_arg = f' --project "{project}"' if project else ' --project "<project-name>"'
    lines = agent_rules_body(vault_arg=vault_arg, project_arg=project_arg)
    if selected_format == AgentClient.CURSOR:
        return "\n".join(
            [
                "---",
                "description: Use Agent Memory CLI for durable project memory",
                "alwaysApply: true",
                "---",
                "",
                *lines,
            ]
        )
    if selected_format == AgentClient.CLAUDE:
        return "\n".join(["# Agent Memory Instructions", "", *lines])
    if selected_format == AgentClient.CODEX:
        return "\n".join(["# Agent Memory Instructions For Codex", "", *lines])
    return "\n".join(["## Agent Memory Usage", "", *lines])


def agent_rules_body(*, vault_arg: str, project_arg: str) -> list[str]:
    build_context = f'memory build-context "<task>"{vault_arg}{project_arg} --task-class planning --json'
    brief = f'memory brief "<topic>"{vault_arg}{project_arg} --json'
    search = f'memory search "<query>"{vault_arg}{project_arg} --json'
    review = f"memory review{vault_arg} --json"
    remember = f'memory remember{vault_arg}{project_arg} --type decision --text "<durable decision>" --json'
    import_source = f"memory import-source <path>{vault_arg}{project_arg} --extract-file <extract.md> --json"
    import_session = f"memory import-session <transcript>{vault_arg}{project_arg} --summary-file <summary.md> --remember-summary --json"
    return [
        "Current product direction is CLI-first and CLI-only for agents. Use only `memory ... --json` commands from any project directory for recall, search, source lookup, imports, writes, review, lifecycle, status, indexing, and session capture.",
        "",
        "Do not read, write, edit, delete, or migrate Agent Memory vault files directly. This includes `Memories/`, `Sources/`, `Briefs/`, `Profiles/`, `Synthesis/`, `raw/`, `.agent-memory/index.sqlite`, cache, embeddings, locks, and schema files. Treat vault paths, SQLite/cache internals, frontmatter, filenames, and generated schema as private storage managed by the CLI.",
        "",
        "If the CLI lacks an operation, stop and report the missing command or product gap. Do not bypass the CLI with direct file edits, SQL, migrations, cache manipulation, or ad hoc scripts.",
        "",
        "Do not run memory recall for every turn. Use memory when the request addresses Toby/Тоби/tb, asks for current facts, decisions, preferences, earlier work, project history/status, or asks to save/analyze durable knowledge.",
        "",
        "When recall is relevant, run:",
        "",
        "```bash",
        build_context,
        "```",
        "",
        "Use returned context only when `memory_needed` is true. Preserve citations when answering or making decisions from recalled memory.",
        "",
        "Toby intent routing examples:",
        "",
        "- `Toby, show current facts about <topic>` / `Тоби, покажи текущие факты по <topic>`: run `memory brief` or `memory search`, then answer with citations.",
        "- `Toby, what did we decide about <topic>` / `Тоби, что мы решили по <topic>`: run `memory build-context`; use returned memory only if `memory_needed=true`.",
        "- `Toby, save this fact/decision/preference` / `Тоби, сохрани это как факт/решение/preference`: create one atomic memory with `memory remember --json`; lifecycle follows `agent_policy`.",
        "- `Toby, review pending memory` / `Тоби, проверь pending memory`: run `memory review --json`, present a compact queue, and ask before approve/reject unless policy allows autonomous action.",
        "- `Toby, update memory for <topic>` / `Тоби, актуализируй память по <topic>`: search related active/pending items, propose supersede/reject/defer/new memory, and ask before lifecycle changes unless policy allows autonomous action.",
        "- `Toby, analyze this source and save it` / `Тоби, проанализируй источник и сохрани`: read/fetch the source, create an extract, preserve the source, then promote only durable atomic items.",
        "",
        "Useful Toby commands:",
        "",
        "```bash",
        brief,
        search,
        remember,
        "```",
        "",
        "Source capture workflow: the AI agent reads or fetches the material first, writes a concise extract, preserves raw/source material through the CLI, then promotes only durable atomic facts, decisions, preferences, project context, or tasks.",
        "",
        "```bash",
        import_source,
        f"memory raw process <raw-path>{vault_arg}{project_arg} --json",
        "```",
        "",
        "Do not store secrets, raw dumps, temporary logs, or unreviewed summaries as canonical memory. Canonical memories should be small, durable, cited when possible, and reviewable.",
        "",
        "Review and lifecycle workflow: agent-created or inferred memories should stay reviewable according to `.agent-memory/config.yaml` policy. Review pending items with:",
        "",
        "```bash",
        review,
        "```",
        "",
        "Present id, type, confidence, source, risk flags, summary, and recommended action. Do not approve, reject, defer, supersede, or mark active without explicit confirmation unless the vault policy allows autonomous lifecycle changes with source, confidence, reason, and audit history.",
        "",
        "Session-end capture workflow: at the end of a substantial task, produce one concise summary of decisions, durable facts, tasks, and open questions. If a transcript/export is available, import it through the CLI and create pending summary memory when useful:",
        "",
        "```bash",
        import_session,
        "```",
        "",
        "Chat-noise reduction: do not narrate every `memory ... --json` call or paste large JSON. Summarize final effects only: source saved, pending memories created, review required, no durable memory found, or CLI gap encountered.",
        "",
        "Scheduled task guidance: confirm source boundaries if ambiguous; fetch only requested sources; never persist secrets, credentials, auth tokens, private personal data, or raw mailbox dumps as canonical memory; create one extract per run; promote only durable atomic items; return source count, pending memory count, and review command.",
        "",
    ]


def managed_content_hash(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


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


def shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def normalize_scheduled_kind(kind: str) -> str:
    """Return a compact, deterministic scheduled source kind slug."""

    selected = re.sub(r"[^a-z0-9_-]+", "_", (kind or "custom").strip().lower()).strip("_-")
    if not selected:
        selected = "custom"
    if not _SCHEDULED_KIND_RE.fullmatch(selected):
        raise ValueError("kind must start with a letter and contain only letters, numbers, underscores, or hyphens")
    return selected


def scheduled_source_channel(kind: str) -> str:
    """Return the source channel recorded for scheduled ingests."""

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
        path = project_path / ".cursor" / "rules" / "agent-memory.mdc"
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
    integration_root = home_path / ".agent-memory" / "integrations"
    if client == AgentClient.CURSOR:
        path = integration_root / "cursor-agent-memory.mdc"
        reason = "safe fallback file for manual Cursor user-level installation"
    elif client == AgentClient.CLAUDE:
        path = integration_root / "CLAUDE.md"
        reason = "safe fallback file for manual Claude user-level installation"
    elif client == AgentClient.CODEX:
        path = integration_root / "codex" / "AGENTS.md"
        reason = "safe fallback file for manual Codex user-level installation"
    else:
        path = integration_root / "AGENTS.md"
        reason = "safe fallback AGENTS.md for manual user-level installation"
    return IntegrationTarget(
        client=client,
        scope=IntegrationScope.USER,
        path=path,
        support=TargetSupport.FALLBACK,
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
