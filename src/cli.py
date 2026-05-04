"""Typer-based command line interface for Memora."""

from __future__ import annotations

import json as json_module
import shutil
import hashlib
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import typer
from rich.console import Console

from agent_integration import (
    agent_doctor_payload as _agent_doctor_payload,
    agent_group_commands_payload as _agent_group_commands_payload,
    agent_group_rules_payload as _agent_group_rules_payload,
    agent_install_commands_payload as _agent_install_commands_payload,
    agent_integrate_payload as _agent_integrate_payload,
    agent_rules_payload as _agent_rules_payload,
    agent_scheduled_template_payload as _agent_scheduled_template_payload,
    agent_session_template_payload as _agent_session_template_payload,
    agent_status_payload as _agent_status_payload,
    agent_targets_payload as _agent_targets_payload,
    agent_update_payload as _agent_update_payload,
    install_agent_rules_payload as _install_agent_rules_payload,
    normalize_scheduled_kind,
    scheduled_source_channel,
)
from brief import brief_memory
from config import ConfigError, load_config, set_agent_aliases
from evaluation import run_evaluation
from freshness import refresh_index_if_needed
from indexer import reindex_vault
from lifecycle import (
    contradict_memories,
    curation_plan,
    decay_memories,
    mark_status,
    reject_memory,
    review_batch_action,
    review_queue,
    supersede_memory,
)
from pdf_import import load_pdf_content
from memora_profile import build_context_profile_payload
from recall import explain_recall, recall_memory
from recall_policy import should_recall
from retrieval import RetrievalIndexError, SearchFilters, search_memory
from safety import scan_source_material
from schema import AuthorKind, LifecycleStatus, MemoryScope, MemoryType
from session import normalize_session_recall_state, session_trace
from slack_import import load_slack_content
from sources import lookup_source, save_source_material
from synthesis import plan_synthesis, write_synthesis
from sync import detect_sync_conflicts
from url_import import fetch_url_content, load_url_content_file, normalize_url
from ux import graph_memory, inspect_memory, open_memory
from vault import (
    doctor_report,
    init_vault,
    placeholder_result,
    remember_memory,
    setup_vault,
    status_summary,
)
from zoom_import import load_zoom_content

app = typer.Typer(
    help="Local-first Obsidian-backed Memora CLI.",
    no_args_is_help=True,
)
raw_app = typer.Typer(help="Stage and inspect raw source material.", no_args_is_help=True)
source_app = typer.Typer(help="Save curated durable source evidence.", no_args_is_help=True)
source_inbox_app = typer.Typer(help="Scan opt-in local source inbox files.", no_args_is_help=True)
review_app = typer.Typer(help="Review pending agent-generated memories.", no_args_is_help=False)
agent_app = typer.Typer(help="Manage coding-agent integrations.", no_args_is_help=True)
session_app = typer.Typer(help="Finalize AI-agent sessions.", no_args_is_help=True)
scheduled_app = typer.Typer(help="Ingest prepared scheduled-agent source material.", no_args_is_help=True)
app.add_typer(raw_app, name="raw")
app.add_typer(source_app, name="source")
app.add_typer(source_inbox_app, name="source-inbox", hidden=True)
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")
app.add_typer(session_app, name="session")
app.add_typer(scheduled_app, name="scheduled", hidden=True)
agent_aliases_app = typer.Typer(
    help="Configure assistant names for recall routing and generated agent rules.",
    no_args_is_help=True,
)
app.add_typer(agent_aliases_app, name="agent-aliases")
console = Console()
SOURCE_INBOX_SUFFIXES = {".md", ".markdown", ".txt"}
SOURCE_INBOX_SCAN_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
SOURCE_INBOX_SCAN_SUPPORTED_SUFFIXES = {*SOURCE_INBOX_SCAN_TEXT_SUFFIXES, ".pdf", ".json"}
RAW_KINDS = {"pdf", "zoom", "slack", "text"}
RAW_FORMATS = {"pdf", "markdown", "json", "txt"}
RAW_READABLE_SUFFIXES = {".md", ".markdown", ".txt", ".json"}
AGENT_CAPTURE_ALLOWED_MEMORY_TYPES = {
    MemoryType.FACT,
    MemoryType.DECISION,
    MemoryType.PREFERENCE,
    MemoryType.TASK,
    MemoryType.PROJECT_CONTEXT,
}
AGENT_SOURCE_SENSITIVITIES = {"normal", "private", "secret", "unsafe"}
HELP_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Setup and health",
        (
            ("init <vault>", "Create the vault layout and config."),
            ("setup [vault]", "Preview or create the vault layout and next setup steps."),
            ("status", "Show vault health and local index state."),
            ("doctor", "Validate Markdown schema, graph links, and conflicts."),
            ("reindex", "Rebuild the disposable SQLite index from Markdown."),
            ("agent rules", "Generate CLI-first instructions for coding agents."),
            ("agent integrate", "Install generated agent instructions into a project."),
            ("agent update", "Update managed agent instructions."),
            ("agent status", "Show installed agent instruction status."),
            ("agent-aliases list", "Show assistant names used for recall routing and agent rules."),
            ("agent-aliases set …", "Save assistant names to the vault config."),
            ("session finalize", "Save an agent transcript, summary, and proposed memories."),
            ("raw add <path>", "Copy one raw file into raw staging with sidecar metadata."),
            ("raw list", "List raw inbox/archive files."),
            ("raw inspect <path>", "Inspect one raw file before processing."),
            ("source add <source.md>", "Save curated source text and optional extract under Sources/."),
        ),
    ),
    (
        "Write and review",
        (
            ("remember", "Create a validated Markdown memory."),
            ("review", "List pending agent-generated memories with a diff-style preview."),
            ("review approve <id...>", "Approve pending agent memories in an explicit batch."),
            ("review reject <id...>", "Reject pending agent memories in an explicit batch."),
        ),
    ),
    (
        "Retrieval and agent context",
        (
            ("search", "Return ranked memory results with snippets and citations."),
            ("recall", "Pack ranked chunks under a strict token budget."),
            ("lookup-source <source_id>", "Return compact evidence from a saved source."),
            ("brief", "Render a citation-preserving Memora Brief."),
            ("build-context", "Apply recall policy and return agent-ready context."),
        ),
    ),
    (
        "Inspect",
        (
            ("inspect <id>", "Show one memory by ID."),
            ("open <id>", "Print a memory Markdown path and Obsidian URI."),
        ),
    ),
)


@app.command("init")
def init_command(
    vault: Path = typer.Argument(..., help="Vault directory to initialize."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Create an Obsidian-compatible vault layout and config."""

    try:
        result = init_vault(vault)
        payload = result.to_dict()
    except Exception as exc:  # pragma: no cover - exercised through CLI error handling
        _handle_error(exc, json_output=json_output, code="init_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Initialized vault:[/green] {payload['vault_path']}")
    if payload["config_created"]:
        console.print(f"Created config: {payload['config_path']}")
    else:
        console.print(f"Preserved existing config: {payload['config_path']}")


@app.command("setup")
def setup_command(
    vault: Optional[Path] = typer.Argument(None, help="Vault directory; defaults to the current directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview setup actions without writing files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Preview or create the default CLI-first Memora vault layout."""

    try:
        selected_vault = vault or Path.cwd()
        payload = setup_vault(selected_vault, dry_run=dry_run).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="setup_failed")

    if json_output:
        _print_json(payload)
        return

    label = "Setup dry run" if payload["dry_run"] else "Setup complete"
    console.print(f"[green]{label}:[/green] {payload['vault_path']}")
    for action in payload["actions"]:
        if action["exists"]:
            continue
        verb = "would create" if payload["dry_run"] else "created"
        console.print(f"- {verb} {action['relative_path']}")
    if not payload["would_write"] and payload["dry_run"]:
        console.print("[green]Vault already has the default layout.[/green]")


@app.command("help")
def help_command(
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show Memora commands grouped by workflow."""

    payload = {
        "ok": True,
        "implemented": True,
        "command": "help",
        "groups": [
            {
                "name": group_name,
                "commands": [
                    {
                        "usage": usage,
                        "description": description,
                    }
                    for usage, description in commands
                ],
            }
            for group_name, commands in HELP_GROUPS
        ],
        "tips": [
            "Run `memora <command> --help` for command-specific options.",
            "Most commands support `--json` for agent-friendly output.",
            "Use `memora agent rules --client cursor` to generate project agent instructions.",
        ],
    }
    if json_output:
        _print_json(payload)
        return

    console.print("[bold]Memora commands[/bold]")
    console.print("Run [cyan]memora <command> --help[/cyan] for command-specific options.")
    console.print("Most commands support [cyan]--json[/cyan].\n")
    for group in payload["groups"]:
        console.print(f"[bold]{group['name']}[/bold]")
        for command in group["commands"]:
            console.print(f"  [cyan]{command['usage']:<24}[/cyan] {command['description']}")
        console.print("")
    console.print("Agent setup: [cyan]memora agent rules --client cursor[/cyan]")


@app.command("agent-rules", hidden=True)
def agent_rules_command(
    rule_format: str = typer.Option("agents", "--format", help="Rule format: agents, cursor, claude, or codex."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in examples."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name to embed in examples."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant name override for this run only (repeat). Default: vault agent_policy.aliases.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Generate CLI-first coding-agent instructions."""

    try:
        payload = _agent_rules_payload(
            rule_format=rule_format,
            vault=vault,
            project=project,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_rules_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo(payload["content"], nl=False)


@app.command("install-agent-rules", hidden=True)
def install_agent_rules_command(
    client: str = typer.Option("agents", "--client", help="Client: agents, cursor, claude, or codex."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory that should receive the rules."),
    target: Optional[Path] = typer.Option(None, "--target", help="Explicit target file path."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in examples."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant name override for generated rules (repeat). Default: vault config.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the write without changing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing target file."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Install generated coding-agent instructions into a project file."""

    try:
        payload = _install_agent_rules_payload(
            client=client,
            project=project,
            target=target,
            vault=vault,
            dry_run=dry_run,
            force=force,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="install_agent_rules_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["dry_run"]:
        if payload["blocked"]:
            console.print(f"[yellow]Dry run:[/yellow] target exists and --force was not passed: {payload['target_path']}")
        else:
            console.print(f"[yellow]Dry run:[/yellow] would write {payload['target_path']}")
        return
    console.print(f"[green]Installed agent rules:[/green] {payload['target_path']}")


@app.command("agent-install-commands", hidden=True)
def agent_install_commands_command(
    project: Path = typer.Option(Path("."), "--project", help="Project directory; defaults to the current directory."),
    client: str = typer.Option(
        "all",
        "--client",
        help="Client command set: all, agents, cursor, claude, or codex.",
    ),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in generated rules."),
    force: bool = typer.Option(False, "--force", help="Include --force in install commands."),
    dry_run_first: bool = typer.Option(
        True,
        "--dry-run-first/--no-dry-run-first",
        help="Include dry-run preview commands before install commands.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Print copy/paste commands that install generated agent rules."""

    try:
        payload = _agent_install_commands_payload(
            project=project,
            client=client,
            vault=vault,
            force=force,
            dry_run_first=dry_run_first,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_install_commands_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo("# Memora agent-rule install commands")
    typer.echo(f"# Project: {payload['project_path']}")
    if payload["vault_path"]:
        typer.echo(f"# Vault: {payload['vault_path']}")
    else:
        typer.echo("# Vault: resolved by --vault, MEMORA_VAULT, or nearest .memora/config.yaml")
    typer.echo("")
    for command in payload["commands"]:
        typer.echo(f"# {command['client']} -> {command['target_path']}")
        if command.get("dry_run_command"):
            typer.echo(command["dry_run_command"])
        typer.echo(command["install_command"])
        typer.echo("")


@agent_app.command("rules")
def agent_group_rules_command(
    client: str = typer.Option("agents", "--client", help="Client: agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Scope for examples: project or user."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in examples."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name to embed in examples."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant name override for this run only (repeat). Default: vault config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Generate CLI-first coding-agent instructions."""

    try:
        payload = _agent_group_rules_payload(
            client=client,
            scope=scope,
            vault=vault,
            project=project,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_rules_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo(payload["content"], nl=False)


@agent_app.command("targets", hidden=True)
def agent_targets_command(
    client: str = typer.Option("all", "--client", help="Client: all, agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Target scope: project or user."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory used for project-scope targets."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show resolved coding-agent integration targets."""

    try:
        payload = _agent_targets_payload(client=client, scope=scope, project=project)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_targets_failed")

    if json_output:
        _print_json(payload)
        return

    for target in payload["targets"]:
        console.print(f"{target['client']}: {target['path']} ({target['support']}, {target['reason']})")


@agent_app.command("integrate")
def agent_integrate_command(
    client: str = typer.Option("all", "--client", help="Client: all, agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Integration scope: project or user."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory used for project-scope targets."),
    target: Optional[Path] = typer.Option(None, "--target", help="Explicit target file path; only valid for one client."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in examples."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant name override for generated rules (repeat). Default: vault config.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview writes without changing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite unmanaged existing target files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Install generated coding-agent instructions for one or more clients."""

    try:
        payload = _agent_integrate_payload(
            client=client,
            scope=scope,
            project=project,
            target=target,
            vault=vault,
            dry_run=dry_run,
            force=force,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_integrate_failed")

    if json_output:
        _print_json(payload)
        return

    _print_agent_operation_results(payload, dry_run_label="Dry run")


@agent_app.command("update")
def agent_update_command(
    client: str = typer.Option("all", "--client", help="Client: all, agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Integration scope: project or user."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory used for project-scope targets."),
    target: Optional[Path] = typer.Option(None, "--target", help="Explicit target file path; only valid for one client."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in examples."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant name override for generated rules (repeat). Default: vault config.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview writes without changing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite unmanaged existing target files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Conservatively update managed coding-agent instructions."""

    try:
        payload = _agent_update_payload(
            client=client,
            scope=scope,
            project=project,
            target=target,
            vault=vault,
            dry_run=dry_run,
            force=force,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_update_failed")

    if json_output:
        _print_json(payload)
        return

    _print_agent_operation_results(payload, dry_run_label="Update dry run")


@agent_app.command("status")
def agent_status_command(
    client: str = typer.Option("all", "--client", help="Client: all, agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Integration scope: project or user."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory used for project-scope targets."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path used to calculate expected content."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant names for expected content hash (repeat). Default: vault config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Report coding-agent integration target status without mutating files."""

    try:
        payload = _agent_status_payload(
            client=client,
            scope=scope,
            project=project,
            vault=vault,
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_status_failed")

    if json_output:
        _print_json(payload)
        return

    for result in payload["results"]:
        console.print(f"{result['client']}: {result['status']} -> {result['target_path']}")


@agent_app.command("doctor", hidden=True)
def agent_doctor_command(
    client: str = typer.Option("all", "--client", help="Client: all, agents, cursor, claude, or codex."),
    scope: str = typer.Option("project", "--scope", help="Integration scope: project or user."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory used for project-scope targets."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to validate when available."),
    alias: list[str] = typer.Option(
        [],
        "--alias",
        "-a",
        help="Assistant names for expected rule content (repeat). Default: vault config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Lightly validate agent integration readiness without mutating files."""

    try:
        payload = _agent_doctor_payload(
            client=client,
            scope=scope,
            project=project,
            vault=vault,
            memora_command_path=shutil.which("memora"),
            vault_probe=_agent_vault_status_probe(vault),
            alias_overrides=alias or None,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_doctor_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["ok"]:
        console.print(f"[green]Agent doctor passed[/green] with {payload['warning_count']} warning(s).")
    else:
        console.print(f"[red]Agent doctor found {payload['issue_count']} issue(s).[/red]")
        raise typer.Exit(1)
    for warning in payload["warnings"]:
        console.print(f"- {warning['code']}: {warning['message']}")


@agent_aliases_app.command("list")
def agent_aliases_list_command(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path; default: resolve from cwd."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show assistant names from agent_policy.aliases."""

    try:
        if vault is not None:
            cfg = load_config(vault.expanduser().resolve())
        else:
            cfg = load_config(start_path=Path.cwd())
        payload = {
            "ok": True,
            "implemented": True,
            "command": "agent-aliases list",
            "vault_path": str(cfg.vault_path),
            "aliases": cfg.agent_policy.aliases,
        }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_aliases_list_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[bold]Assistant aliases[/bold] ({payload['vault_path']}):")
    for name in payload["aliases"]:
        console.print(f"  - {name}")


@agent_aliases_app.command("set")
def agent_aliases_set_command(
    names: list[str] = typer.Argument(..., help="Distinct assistant names in display order."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path; default: resolve from cwd."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Persist assistant names to .memora/config.yaml (agent_policy.aliases)."""

    try:
        if vault is not None:
            vault_path = vault.expanduser().resolve()
        else:
            vault_path = load_config(start_path=Path.cwd()).vault_path
        updated = set_agent_aliases(vault_path, names)
        payload = {
            "ok": True,
            "implemented": True,
            "command": "agent-aliases set",
            "vault_path": str(vault_path),
            "aliases": updated,
        }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_aliases_set_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Updated assistant aliases[/green] for {payload['vault_path']}:")
    for name in payload["aliases"]:
        console.print(f"  - {name}")


@agent_app.command("commands", hidden=True)
def agent_group_commands_command(
    project: Path = typer.Option(Path("."), "--project", help="Project directory; defaults to the current directory."),
    client: str = typer.Option("all", "--client", help="Client command set: all, agents, cursor, claude, or codex."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path to embed in generated rules."),
    force: bool = typer.Option(False, "--force", help="Include --force in install commands."),
    dry_run_first: bool = typer.Option(
        True,
        "--dry-run-first/--no-dry-run-first",
        help="Include dry-run preview commands before install commands.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Print copy/paste commands that install generated agent rules."""

    try:
        payload = _agent_group_commands_payload(
            project=project,
            client=client,
            vault=vault,
            force=force,
            dry_run_first=dry_run_first,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_commands_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo("# Memora agent integration commands")
    typer.echo(f"# Project: {payload['project_path']}")
    if payload["vault_path"]:
        typer.echo(f"# Vault: {payload['vault_path']}")
    else:
        typer.echo("# Vault: resolved by --vault, MEMORA_VAULT, or nearest .memora/config.yaml")
    typer.echo("")
    for command in payload["commands"]:
        typer.echo(f"# {command['client']} -> {command['target_path']}")
        if command.get("dry_run_command"):
            typer.echo(command["dry_run_command"])
        typer.echo(command["install_command"])
        typer.echo("")


@agent_app.command("scheduled-template", hidden=True)
def agent_scheduled_template_command(
    kind: str = typer.Option("custom", "--kind", help="Template kind: email, calendar, slack, web, or custom."),
    client: str = typer.Option("agents", "--client", help="Client: agents, cursor, claude, or codex."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name to embed in the template."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Render a minimal scheduled-memory task template."""

    try:
        payload = _agent_scheduled_template_payload(client=client, kind=kind, project=project)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_scheduled_template_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo(payload["content"])


@agent_app.command("session-template", hidden=True)
def agent_session_template_command(
    client: str = typer.Option("agents", "--client", help="Client: agents, cursor, claude, or codex."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name to embed in the template."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Render a minimal session-end capture template."""

    try:
        payload = _agent_session_template_payload(client=client, project=project)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_session_template_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo(payload["content"])


@agent_app.command("capture", hidden=True)
def agent_capture_command(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for source and memories."),
    source_title: Optional[str] = typer.Option(None, "--source-title", help="Title for the saved source material."),
    source_file: Path = typer.Option(..., "--source-file", help="Already-read raw source material file."),
    summary_file: Path = typer.Option(..., "--summary-file", help="Agent-authored source extract/summary file."),
    memories_file: Path = typer.Option(..., "--memories-file", help="JSON list or object with proposed memories."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    confidence: float = typer.Option(0.75, "--confidence", min=0, max=1, help="Default confidence for proposals."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview without writing to the vault."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Batch-save an agent-analyzed source and pending atomic memories."""

    try:
        config = load_config(vault)
        payload = _agent_capture_payload(
            config,
            source_title=source_title,
            source_file=source_file,
            summary_file=summary_file,
            memories_file=memories_file,
            project=project,
            tags=tag,
            sensitivity=sensitivity,
            confidence=confidence,
            dry_run=dry_run,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="agent_capture_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(
            f"[yellow]Dry run:[/yellow] would save source and {payload['memory_count']} pending memory proposal(s)."
        )
        return
    console.print(f"[green]Captured source:[/green] {payload['source']['relative_source_path']}")
    console.print(f"Pending memories: {payload['pending_count']}")


@session_app.command("finalize")
def session_finalize_command(
    transcript_arg: Optional[Path] = typer.Argument(None, help="AI-agent transcript/session file."),
    transcript_option: Optional[Path] = typer.Option(None, "--transcript", help="AI-agent transcript/session file."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    summary_file: Path = typer.Option(..., "--summary-file", help="Agent-authored concise session summary file."),
    memories_file: Optional[Path] = typer.Option(None, "--memories-file", help="JSON list or object with proposed memories."),
    session_format: str = typer.Option("text", "--format", help="Transcript format metadata."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for source and memories."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    confidence: float = typer.Option(0.75, "--confidence", min=0, max=1, help="Default confidence for proposed memories."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview without writing to the vault."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Finalize an AI-agent session with a source, summary memory, and proposals."""

    try:
        transcript = _resolve_session_transcript(transcript_arg, transcript_option)
        config = load_config(vault)
        payload = _session_finalize_payload(
            config,
            transcript=transcript,
            summary_file=summary_file,
            memories_file=memories_file,
            session_format=session_format,
            project=project,
            tags=tag,
            sensitivity=sensitivity,
            confidence=confidence,
            dry_run=dry_run,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="session_finalize_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(
            f"[yellow]Dry run:[/yellow] would save session and {payload['pending_count']} pending memory item(s)."
        )
        return
    console.print(f"[green]Finalized session source:[/green] {payload['source']['relative_source_path']}")
    console.print(f"Pending memories: {payload['pending_count']}")


@scheduled_app.command("ingest", hidden=True)
def scheduled_ingest_command(
    kind: str = typer.Option("custom", "--kind", help="Scheduled source kind, e.g. email, calendar, slack, web, or custom."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for source and memories."),
    source_file: Path = typer.Option(..., "--source-file", help="Already exported scheduled source material file."),
    extract_file: Path = typer.Option(..., "--extract-file", help="Agent-authored source extract/summary file."),
    memories_file: Path = typer.Option(..., "--memories-file", help="JSON list or object with proposed memories."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    confidence: float = typer.Option(0.75, "--confidence", min=0, max=1, help="Default confidence for proposed memories."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview without writing to the vault."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Ingest prepared scheduled-agent material without fetching provider data."""

    try:
        config = load_config(vault)
        payload = _scheduled_ingest_payload(
            config,
            kind=kind,
            project=project,
            source_file=source_file,
            extract_file=extract_file,
            memories_file=memories_file,
            tags=tag,
            sensitivity=sensitivity,
            confidence=confidence,
            dry_run=dry_run,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="scheduled_ingest_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(
            f"[yellow]Dry run:[/yellow] would ingest scheduled {payload['kind']} source "
            f"and {payload['memory_count']} pending memory proposal(s)."
        )
        return
    console.print(f"[green]Ingested scheduled source:[/green] {payload['source']['relative_source_path']}")
    console.print(f"Pending memories: {payload['pending_count']}")


@raw_app.command("list")
def raw_list_command(
    path: Optional[Path] = typer.Argument(None, help="Raw directory to list; defaults to <vault>/raw."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List files in the raw inbox/archive layer."""

    try:
        config = load_config(vault)
        raw_path = _resolve_raw_path(config, path)
        candidates = _raw_files(raw_path)
        payload = {
            "ok": True,
            "implemented": True,
            "command": "raw list",
            "raw_path": str(raw_path),
            "relative_path": _relative_to_vault(config, raw_path),
            "file_count": len(candidates),
            "files": [_raw_file_payload(config, candidate, include_preview=False) for candidate in candidates],
        }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="raw_list_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Raw files:[/green] {payload['file_count']} [dim]{payload['relative_path']}[/dim]")
    for item in payload["files"]:
        marker = "" if item["processable"] else " [yellow](unsupported)[/yellow]"
        console.print(f"- {item['relative_path']}{marker}")


@raw_app.command("add")
def raw_add_command(
    path: Path = typer.Argument(..., help="Local file to copy into raw staging."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    kind: str = typer.Option(..., "--kind", help="Raw source kind: pdf, zoom, slack, or text."),
    source_format: str = typer.Option(..., "--format", help="Raw file format: pdf, markdown, json, or txt."),
    title: Optional[str] = typer.Option(None, "--title", help="Optional human title for the raw material."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the raw material."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the staging plan without copying files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Copy one raw file into raw staging with metadata only."""

    try:
        config = load_config(vault)
        payload = _raw_add_payload(
            config,
            path,
            kind=kind,
            source_format=source_format,
            title=title,
            project=project,
            sensitivity=sensitivity,
            tags=tag,
            dry_run=dry_run,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="raw_add_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["dry_run"]:
        console.print(f"[yellow]Dry run:[/yellow] would stage raw file at {payload['relative_path']}")
        return
    console.print(f"[green]Staged raw file:[/green] {payload['relative_path']}")
    console.print(f"Metadata: {payload['relative_metadata_path']}")


@raw_app.command("inspect")
def raw_inspect_command(
    path: Path = typer.Argument(..., help="Raw file to inspect."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Inspect one raw file before processing."""

    try:
        config = load_config(vault)
        raw_path = _resolve_raw_path(config, path)
        if not raw_path.is_file():
            raise ValueError(f"raw file not found: {raw_path}")
        payload = {
            "ok": True,
            "implemented": True,
            "command": "raw inspect",
            **_raw_file_payload(config, raw_path, include_preview=True),
        }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="raw_inspect_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[bold]{payload['relative_path']}[/bold]")
    console.print(f"Size: {payload['size_bytes']} bytes")
    console.print(f"Hash: {payload['content_hash']}")
    console.print(f"Processable: {payload['processable']}")
    if payload.get("preview"):
        console.print("")
        console.print(payload["preview"], markup=False, soft_wrap=True)


@raw_app.command("process", hidden=True)
def raw_process_command(
    path: Path = typer.Argument(..., help="Raw Markdown/text file to normalize into Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to file stem."),
    extract_file: Optional[Path] = typer.Option(None, "--extract-file", help="Optional Markdown/text extract file."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    channel: str = typer.Option("manual", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("user_provided", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the normalization plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Normalize one raw file into Sources/ without creating canonical memories."""

    try:
        config = load_config(vault)
        raw_path = _resolve_raw_path(config, path)
        payload = _raw_process_one(
            config,
            raw_path,
            title=title,
            extract_file=extract_file,
            project=project,
            channel=channel,
            source_quality=source_quality,
            sensitivity=sensitivity,
            tags=tag,
            dry_run=dry_run,
        )
        payload["command"] = "raw process"
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="raw_process_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["dry_run"]:
        console.print(f"[yellow]Dry run:[/yellow] {payload['relative_path']} -> Sources/")
        return
    console.print(f"[green]Saved source:[/green] {payload['relative_source_path']}")


@raw_app.command("process-inbox", hidden=True)
def raw_process_inbox_command(
    path: Optional[Path] = typer.Argument(None, help="Raw inbox directory; defaults to <vault>/raw/inbox."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for imported sources."),
    channel: str = typer.Option("manual", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("user_provided", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum number of files to process."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the normalization plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Normalize Markdown/text files from raw/inbox into Sources/."""

    try:
        config = load_config(vault)
        inbox_path = _resolve_raw_path(config, path) if path is not None else config.raw_root / "inbox"
        candidates = [candidate for candidate in _raw_files(inbox_path) if _is_processable_raw(candidate)]
        if limit is not None:
            candidates = candidates[:limit]
        if dry_run:
            payload = {
                "ok": True,
                "implemented": True,
                "command": "raw process-inbox",
                "dry_run": True,
                "inbox_path": str(inbox_path),
                "relative_path": _relative_to_vault(config, inbox_path),
                "source_count": len(candidates),
                "sources": [_raw_file_payload(config, candidate, include_preview=False) for candidate in candidates],
            }
        else:
            sources = [
                _raw_process_one(
                    config,
                    candidate,
                    title=None,
                    extract_file=None,
                    project=project,
                    channel=channel,
                    source_quality=source_quality,
                    sensitivity=sensitivity,
                    tags=tag,
                    dry_run=False,
                )
                for candidate in candidates
            ]
            payload = {
                "ok": True,
                "implemented": True,
                "command": "raw process-inbox",
                "dry_run": False,
                "inbox_path": str(inbox_path),
                "relative_path": _relative_to_vault(config, inbox_path),
                "source_count": len(sources),
                "sources": sources,
            }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="raw_process_inbox_failed")

    if json_output:
        _print_json(payload)
        return

    action = "Would process" if payload["dry_run"] else "Processed"
    console.print(f"[green]{action} {payload['source_count']} raw file(s)[/green]")


@source_app.command("add")
def source_add_command(
    path: Path = typer.Argument(..., help="Markdown/text source file to save under Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    extract: Optional[Path] = typer.Option(None, "--extract", "--extract-file", help="Optional extract Markdown/text file."),
    kind: str = typer.Option("text", "--kind", help="Source kind: pdf, zoom, slack, or text."),
    source_format: str = typer.Option("markdown", "--format", help="Source format: markdown, json, txt, or pdf."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to file stem."),
    url: Optional[str] = typer.Option(None, "--url", help="Optional source URL or permalink."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Save curated source text and optional agent-authored extract."""

    try:
        config = load_config(vault)
        payload = _source_add_payload(
            config,
            path,
            extract=extract,
            kind=kind,
            source_format=source_format,
            title=title,
            url=url,
            project=project,
            sensitivity=sensitivity,
            tags=tag,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="source_add_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Saved source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command()
def remember(
    memory_type: MemoryType = typer.Option(..., "--type", help="Memory type."),
    text: str = typer.Option(..., "--text", help="Memory body text."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope."),
    project: Optional[str] = typer.Option(None, "--project", help="Project name for project scope."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Create a validated Markdown memory file."""

    try:
        config = load_config(vault)
        result = remember_memory(
            config,
            memory_type=memory_type,
            text=text,
            scope=scope,
            project=project,
            status=status,
            tags=tag,
        )
        payload = result.to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="remember_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Created memory:[/green] {payload['relative_path']}")


@app.command("import-source", hidden=True)
def import_source_command(
    path: Path = typer.Argument(..., help="Markdown or text file to save under Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to file stem."),
    extract_file: Optional[Path] = typer.Option(None, "--extract-file", help="Optional Markdown/text extract file."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    channel: str = typer.Option("file", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("imported_export", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Save a Markdown/text file as source material without promoting memory."""

    try:
        config = load_config(vault)
        content = path.expanduser().read_text(encoding="utf-8")
        extract = extract_file.expanduser().read_text(encoding="utf-8") if extract_file else None
        result = save_source_material(
            config,
            title=title or path.stem,
            content=content,
            extract=extract,
            project=project,
            tags=tag,
            channel=channel,
            source_quality=source_quality,
            sensitivity=sensitivity,
            origin={
                "provider": "file",
                "file_name": path.name,
                "path": str(path.expanduser()),
            },
        )
        payload = result.to_dict()
        payload.update({"command": "import-source"})
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_source_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Imported source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command("import-source-inbox", hidden=True)
def import_source_inbox_command(
    path: Path = typer.Argument(..., help="Directory containing Markdown/text source files."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for imported sources."),
    channel: str = typer.Option("web_clipper", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("imported_export", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show matching files without writing to the vault."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Import Markdown/text files from a source inbox directory."""

    try:
        config = load_config(vault)
        inbox = path.expanduser()
        if not inbox.is_dir():
            raise ValueError(f"source inbox is not a directory: {inbox}")
        candidates = _source_inbox_files(inbox)
        if dry_run:
            payload = _source_inbox_plan_payload(inbox, candidates, dry_run=True)
        else:
            sources = []
            for candidate in candidates:
                result = save_source_material(
                    config,
                    title=candidate.stem,
                    content=candidate.read_text(encoding="utf-8"),
                    project=project,
                    tags=tag,
                    channel=channel,
                    source_quality=source_quality,
                    sensitivity=sensitivity,
                    origin={
                        "provider": "file",
                        "file_name": candidate.name,
                        "path": str(candidate),
                    },
                )
                sources.append(result.to_dict())
            payload = {
                "ok": True,
                "implemented": True,
                "command": "import-source-inbox",
                "dry_run": False,
                "inbox_path": str(inbox),
                "source_count": len(sources),
                "sources": sources,
            }
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_source_inbox_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] {payload['source_count']} file(s) would be imported.")
        for source in payload["sources"]:
            console.print(f"- {source['path']}")
        return

    console.print(f"[green]Imported sources:[/green] {payload['source_count']}")
    for source in payload["sources"]:
        console.print(f"- {source['relative_source_path']}")


@source_inbox_app.command("scan", hidden=True)
def source_inbox_scan_command(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    path: Optional[Path] = typer.Option(None, "--path", "--inbox", help="Override configured source inbox path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for imported sources."),
    sensitivity: Optional[str] = typer.Option(None, "--sensitivity", help="Override configured sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum number of importable files to process."),
    once: bool = typer.Option(False, "--once", help="Run one scan and exit; this is the default behavior."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan supported imports without writing to the vault."),
    ignore_disabled: bool = typer.Option(
        False,
        "--ignore-disabled",
        help="Explicitly scan even when source connector config is disabled.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Run a one-shot scan of opt-in local source inbox files."""

    try:
        config = load_config(vault)
        payload = _source_inbox_scan_payload(
            config,
            path=path,
            project=project,
            sensitivity=sensitivity,
            tags=tag,
            limit=limit,
            dry_run=dry_run,
            ignore_disabled=ignore_disabled,
        )
        payload["once"] = True
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="source_inbox_scan_failed")

    if json_output:
        _print_json(payload)
        if not payload["ok"]:
            raise typer.Exit(1)
        return

    label = "Dry run" if dry_run else "Source inbox scan"
    console.print(f"[green]{label}:[/green] {payload['inbox_path']}")
    if payload["planned_count"]:
        console.print(f"Planned imports: {payload['planned_count']}")
        for item in payload["planned"]:
            console.print(f"- {item['relative_path']} via {item['connector']}")
    if payload["imported_count"]:
        console.print(f"Imported sources: {payload['imported_count']}")
        for item in payload["imported"]:
            console.print(f"- {item['relative_source_path']}")
    if payload["skipped_count"]:
        console.print(f"Skipped files: {payload['skipped_count']}")
        for item in payload["skipped"]:
            console.print(f"- {item['relative_path']}: {item['reason']}")
    if payload["error_count"]:
        console.print(f"[red]Errors:[/red] {payload['error_count']}")
        for item in payload["errors"]:
            console.print(f"- {item['relative_path']}: {item['message']}")
        raise typer.Exit(1)


@app.command("import-url", hidden=True)
def import_url_command(
    url: str = typer.Argument(..., help="Explicit http(s) URL to import into Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to the HTML title or URL."),
    from_file: Optional[Path] = typer.Option(
        None,
        "--from-file",
        "--content-file",
        help="Read saved HTML/text from a local file instead of fetching the URL.",
    ),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    channel: str = typer.Option("url", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("agent_fetched", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the URL import plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Fetch or import explicit URL content as reviewable source material."""

    try:
        config = load_config(vault)
        normalized_url = normalize_url(url)
        if dry_run:
            payload = _url_import_plan_payload(
                config,
                url=normalized_url,
                title=title,
                from_file=from_file,
                project=project,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                tags=tag,
            )
        else:
            imported = (
                load_url_content_file(normalized_url, from_file)
                if from_file is not None
                else fetch_url_content(normalized_url)
            )
            result = save_source_material(
                config,
                title=title or imported.title,
                url=normalized_url,
                content=imported.content,
                extract=imported.extract,
                project=project,
                tags=tag,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                origin=imported.origin,
            )
            payload = result.to_dict()
            payload.update(
                {
                    "command": "import-url",
                    "dry_run": False,
                    "content": imported.summary(),
                    "next_steps": [
                        "Review the saved source and extract before promoting canonical memory.",
                        "Create pending source-backed memories only for durable facts, decisions, preferences, project context, or tasks.",
                    ],
                }
            )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_url_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] URL source would be imported: {payload['url']}")
        return

    console.print(f"[green]Imported URL source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command("import-pdf", hidden=True)
def import_pdf_command(
    path: Path = typer.Argument(..., help="Explicit local PDF file to import into Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to the PDF file stem."),
    text_file: Optional[Path] = typer.Option(
        None,
        "--text-file",
        "--content-file",
        help="Read pre-extracted PDF text from a UTF-8 file instead of using the optional extractor.",
    ),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    channel: str = typer.Option("pdf", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("user_provided", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the PDF import plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Import explicit local PDF text as reviewable source material."""

    try:
        config = load_config(vault)
        if dry_run:
            payload = _pdf_import_plan_payload(
                config,
                path=path,
                title=title,
                text_file=text_file,
                project=project,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                tags=tag,
            )
        else:
            imported = load_pdf_content(path, text_file=text_file)
            result = save_source_material(
                config,
                title=title or imported.title,
                content=imported.content,
                extract=imported.extract,
                project=project,
                tags=tag,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                origin=imported.origin,
            )
            payload = result.to_dict()
            payload.update(
                {
                    "command": "import-pdf",
                    "dry_run": False,
                    "content": imported.summary(),
                    "next_steps": [
                        "Review the saved PDF source and extract before promoting canonical memory.",
                        "Create pending source-backed memories only for durable facts, decisions, preferences, project context, or tasks.",
                    ],
                }
            )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_pdf_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] PDF source would be imported: {payload['path']}")
        return

    console.print(f"[green]Imported PDF source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command("import-zoom", hidden=True)
def import_zoom_command(
    path: Path = typer.Argument(..., help="Explicit local Zoom summary/transcript export to import into Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to export heading or file stem."),
    meeting_date: Optional[str] = typer.Option(None, "--meeting-date", help="Meeting date metadata."),
    meeting_time: Optional[str] = typer.Option(None, "--meeting-time", help="Meeting time metadata."),
    meeting_id: Optional[str] = typer.Option(None, "--meeting-id", help="Zoom meeting ID metadata."),
    meeting_url: Optional[str] = typer.Option(None, "--meeting-url", help="Zoom meeting URL metadata."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    channel: str = typer.Option("zoom", "--channel", help="Source channel metadata."),
    source_quality: str = typer.Option("meeting_summary", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the Zoom import plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Import an explicit local Zoom meeting summary/transcript export."""

    try:
        config = load_config(vault)
        imported = load_zoom_content(
            path,
            title=title,
            meeting_date=meeting_date,
            meeting_time=meeting_time,
            meeting_id=meeting_id,
            meeting_url=meeting_url,
        )
        if dry_run:
            payload = _zoom_import_plan_payload(
                config,
                imported=imported,
                project=project,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                tags=tag,
            )
        else:
            result = save_source_material(
                config,
                title=imported.title,
                url=_meeting_url(imported.meeting),
                content=imported.content,
                extract=imported.extract,
                project=project,
                tags=tag,
                channel=channel,
                source_quality=source_quality,
                sensitivity=sensitivity,
                origin=imported.origin,
            )
            payload = result.to_dict()
            payload.update(
                {
                    "command": "import-zoom",
                    "dry_run": False,
                    "content": imported.summary(),
                    "meeting": imported.meeting,
                    "next_steps": [
                        "Review the saved Zoom source and extract before promoting canonical memory.",
                        "Create pending source-backed memories only for durable facts, decisions, preferences, project context, or tasks.",
                    ],
                }
            )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_zoom_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] Zoom source would be imported: {payload['path']}")
        return

    console.print(f"[green]Imported Zoom source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command("import-slack", hidden=True)
def import_slack_command(
    path: Path = typer.Argument(..., help="Explicit local Slack thread/message export to import into Sources/."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    title: Optional[str] = typer.Option(None, "--title", help="Source title; defaults to Slack metadata or file stem."),
    slack_channel: Optional[str] = typer.Option(None, "--channel", help="Slack channel name or ID metadata."),
    thread_ts: Optional[str] = typer.Option(None, "--thread-ts", help="Slack thread timestamp metadata."),
    permalink: Optional[str] = typer.Option(None, "--permalink", help="Slack message or thread permalink metadata."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the source."),
    source_quality: str = typer.Option("chat_thread", "--source-quality", help="Source quality metadata."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the Slack import plan without writing Sources/."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Import an explicit local Slack thread/message export."""

    try:
        config = load_config(vault)
        imported = load_slack_content(
            path,
            title=title,
            channel=slack_channel,
            thread_ts=thread_ts,
            permalink=permalink,
        )
        if dry_run:
            payload = _slack_import_plan_payload(
                config,
                imported=imported,
                project=project,
                source_quality=source_quality,
                sensitivity=sensitivity,
                tags=tag,
            )
        else:
            result = save_source_material(
                config,
                title=imported.title,
                url=_thread_permalink(imported.thread),
                content=imported.content,
                extract=imported.extract,
                project=project,
                tags=tag,
                channel="slack",
                source_quality=source_quality,
                sensitivity=sensitivity,
                origin=imported.origin,
            )
            payload = result.to_dict()
            payload.update(
                {
                    "command": "import-slack",
                    "dry_run": False,
                    "content": imported.summary(),
                    "thread": imported.thread,
                    "next_steps": [
                        "Review the saved Slack source and extract before promoting canonical memory.",
                        "Create pending source-backed memories only for durable facts, decisions, preferences, project context, or tasks.",
                    ],
                }
            )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_slack_failed")

    if json_output:
        _print_json(payload)
        return

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] Slack source would be imported: {payload['path']}")
        return

    console.print(f"[green]Imported Slack source:[/green] {payload['relative_source_path']}")
    if payload.get("relative_extract_path"):
        console.print(f"Extract: {payload['relative_extract_path']}")


@app.command("import-session", hidden=True)
def import_session_command(
    path: Path = typer.Argument(..., help="AI-agent transcript/session file."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    summary_file: Optional[Path] = typer.Option(None, "--summary-file", help="Optional concise session summary file."),
    remember_summary: bool = typer.Option(False, "--remember-summary", help="Create a pending conversation_summary memory."),
    session_format: str = typer.Option("text", "--format", help="Transcript format metadata, e.g. cursor-jsonl."),
    project: Optional[str] = typer.Option(None, "--project", help="Project metadata for the session."),
    sensitivity: str = typer.Option("normal", "--sensitivity", help="Sensitivity metadata."),
    tag: list[str] = typer.Option([], "--tag", help="Tag to add; may be repeated."),
    confidence: float = typer.Option(0.75, "--confidence", min=0, max=1, help="Confidence for summary memory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Save an AI-agent session transcript and optional pending summary memory."""

    try:
        if remember_summary and summary_file is None:
            raise ValueError("--remember-summary requires --summary-file")
        config = load_config(vault)
        transcript = path.expanduser().read_text(encoding="utf-8")
        summary = summary_file.expanduser().read_text(encoding="utf-8") if summary_file else None
        saved_source = save_source_material(
            config,
            title=path.stem,
            content=transcript,
            extract=summary,
            project=project,
            tags=[*tag, "ai-session"],
            channel="ai_session",
            source_quality="imported_export",
            sensitivity=sensitivity,
            origin={
                "provider": "file",
                "file_name": path.name,
                "path": str(path.expanduser()),
                "format": session_format,
            },
        )
        payload: dict[str, Any] = {
            "ok": True,
            "implemented": True,
            "command": "import-session",
            "source": saved_source.to_dict(),
            "memory": None,
        }
        if remember_summary:
            source_path = saved_source.relative_extract_path or saved_source.relative_source_path
            memory = remember_memory(
                config,
                memory_type=MemoryType.CONVERSATION_SUMMARY,
                text=summary or "",
                scope=MemoryScope.PROJECT if project else None,
                project=project,
                status=LifecycleStatus.PENDING,
                tags=[*tag, "ai-session"],
                author_kind=AuthorKind.AGENT,
                author_name="session import",
                source={
                    "path": source_path.as_posix(),
                    "title": saved_source.title,
                    "source_id": saved_source.source_id,
                },
                confidence=confidence,
                risk_flags=saved_source.risk_flags,
            )
            payload["memory"] = memory.to_dict()
            payload["review_required"] = memory.status == LifecycleStatus.PENDING
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="import_session_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Imported session source:[/green] {payload['source']['relative_source_path']}")
    if payload.get("memory"):
        console.print(f"Pending summary memory: {payload['memory']['relative_path']}")


@app.command()
def reindex(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    clean: bool = typer.Option(False, "--clean", help="Delete the existing SQLite index before rebuilding."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Rebuild the local SQLite index from Markdown."""

    try:
        config = load_config(vault)
        payload = reindex_vault(config, clean=clean).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="reindex_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Indexed vault:[/green] {payload['index_path']}")
    console.print(
        f"Documents: {payload['documents_indexed']} indexed, "
        f"{payload['documents_skipped']} skipped, {payload['documents_removed']} removed"
    )
    console.print(f"Chunks: {payload['chunks_indexed']} indexed, {payload['chunks_skipped']} skipped")
    if not payload["graph_ok"]:
        console.print(f"[yellow]Graph warnings:[/yellow] {payload['orphan_count']} orphan relation(s)")


@app.command("refresh-index", hidden=True)
def refresh_index(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    debounce_seconds: Optional[float] = typer.Option(
        None,
        "--debounce",
        min=0,
        help="Seconds of quiet time before reindexing. Defaults to config.",
    ),
    clean: Optional[bool] = typer.Option(
        None,
        "--clean/--no-clean",
        help="Override whether freshness-triggered reindex deletes SQLite first.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Refresh the local index when durable Markdown/config/schema files changed."""

    try:
        config = load_config(vault)
        payload = refresh_index_if_needed(config, debounce_seconds=debounce_seconds, clean=clean).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="refresh_index_failed")

    if json_output:
        _print_json(payload)
        return

    if not payload["enabled"]:
        console.print("[yellow]Index freshness watcher is disabled.[/yellow]")
    elif payload["reindexed"]:
        change_count = payload["changes"]["change_count"]
        console.print(f"[green]Refreshed index[/green] after {change_count} durable file change(s).")
    else:
        console.print(f"[green]Index is fresh[/green]: checked {payload['checked_files']} durable file(s).")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    created_after: Optional[str] = typer.Option(None, "--created-after", help="Created-at lower bound."),
    created_before: Optional[str] = typer.Option(None, "--created-before", help="Created-at upper bound."),
    updated_after: Optional[str] = typer.Option(None, "--updated-after", help="Updated-at lower bound."),
    updated_before: Optional[str] = typer.Option(None, "--updated-before", help="Updated-at upper bound."),
    valid_from: Optional[str] = typer.Option(None, "--valid-from", help="Valid-from lower bound date."),
    valid_to: Optional[str] = typer.Option(None, "--valid-to", help="Valid-to upper bound date."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this query.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    refresh: Optional[bool] = typer.Option(
        None,
        "--refresh/--no-refresh",
        help="Refresh the index before search; defaults to index_freshness config.",
    ),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Search indexed memory using keyword, optional semantic, metadata, and graph signals."""

    try:
        config = load_config(vault)
        freshness = _maybe_refresh_index(config, before="search", refresh=refresh)
        filters = SearchFilters(
            project=project,
            memory_type=memory_type.value if memory_type else None,
            status=status.value if status else None,
            scope=scope.value if scope else None,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        payload = search_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(filters.to_dict()),
            include_related=include_related,
            limit=limit,
            semantic=semantic,
            mode=mode,
        ).to_dict()
        payload["freshness"] = freshness
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "search_failed",
        )

    if json_output:
        _print_json(payload)
        return

    semantic_info = payload.get("semantic", {})
    if semantic_info.get("enabled"):
        console.print(
            "[dim]Semantic: "
            f"provider={semantic_info.get('provider') or '-'} "
            f"model={semantic_info.get('model') or '-'}[/dim]"
        )

    if not payload["results"]:
        console.print("[yellow]No memories found.[/yellow]")
        return

    for position, result in enumerate(payload["results"], start=1):
        metadata = result["metadata"]
        related_marker = " [cyan](related)[/cyan]" if result["related"] else ""
        console.print(
            f"[bold]{position}. {result['id']}[/bold]{related_marker} "
            f"[dim]score={result['score']:.2f} path={result['path']}[/dim]"
        )
        console.print(
            f"   {metadata['type']} / {metadata['status']} / "
            f"project={metadata['project'] or '-'} / chunk={metadata['chunk_type']}"
        )
        console.print(f"   {result['snippet']}")


@app.command()
def recall(
    query: str = typer.Argument(..., help="Recall query."),
    budget: Optional[int] = typer.Option(None, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    task_class: str = typer.Option("default", "--task-class", help="Recall policy class: default, coding, planning, or review."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this query.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Client-controlled recall session id."),
    loaded_memory_id: list[str] = typer.Option(
        [],
        "--loaded-memory-id",
        help="Memory id already loaded in this session; may be repeated or comma-separated.",
    ),
    loaded_source_id: list[str] = typer.Option(
        [],
        "--loaded-source-id",
        help="Source id already loaded in this session; may be repeated or comma-separated.",
    ),
    refresh: Optional[bool] = typer.Option(
        None,
        "--refresh/--no-refresh",
        help="Refresh the index before recall; defaults to index_freshness config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Recall ranked memory chunks packed under a strict token budget."""

    try:
        config = load_config(vault)
        selected_task_class, task_policy = _resolve_task_policy(config, task_class)
        selected_budget = _cli_budget(budget, task_policy)
        selected_include_related = include_related or task_policy.include_related
        freshness = _maybe_refresh_index(config, before="recall", refresh=refresh)
        filters = SearchFilters(
            project=project,
            memory_type=memory_type.value if memory_type else None,
            status=status.value if status else None,
            scope=scope.value if scope else None,
        )
        payload = recall_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(filters.to_dict()),
            budget=selected_budget,
            include_related=selected_include_related,
            semantic=semantic,
            mode=mode,
            session_id=session_id,
            loaded_memory_ids=loaded_memory_id,
            loaded_source_ids=loaded_source_id,
        ).to_dict()
        payload.update(
            {
                "task_class": selected_task_class,
                "recall_policy": task_policy.model_dump(mode="json"),
                "freshness": freshness,
            }
        )
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "recall_failed",
        )

    if json_output:
        _print_json(payload)
        return

    if not payload["chunks"]:
        console.print("[yellow]No memories packed.[/yellow]")
        return

    console.print(
        f"[green]Packed {payload['chunk_count']} chunk(s)[/green] "
        f"using {payload['used_tokens_estimate']}/{payload['budget']} estimated tokens"
    )
    for position, chunk in enumerate(payload["chunks"], start=1):
        metadata = chunk["metadata"]
        truncated = " [yellow](truncated)[/yellow]" if chunk["truncated"] else ""
        console.print(
            f"[bold]{position}. {chunk['id']}[/bold]{truncated} "
            f"[dim]tokens={chunk['token_estimate']} path={chunk['path']}[/dim]"
        )
        console.print(
            f"   {metadata['type']} / {metadata['status']} / "
            f"project={metadata['project'] or '-'} / chunk={chunk['chunk_type']}"
        )
        console.print(f"   {chunk['text']}")


@app.command("explain-recall", hidden=True)
def explain_recall_command(
    query: str = typer.Argument(..., help="Recall query to explain."),
    budget: Optional[int] = typer.Option(None, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    task_class: str = typer.Option("default", "--task-class", help="Recall policy class: default, coding, planning, or review."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this explanation.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    refresh: Optional[bool] = typer.Option(
        None,
        "--refresh/--no-refresh",
        help="Refresh the index before explanation; defaults to index_freshness config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Explain why recall selected or skipped candidate chunks."""

    try:
        config = load_config(vault)
        selected_task_class, task_policy = _resolve_task_policy(config, task_class)
        selected_budget = _cli_budget(budget, task_policy)
        selected_include_related = include_related or task_policy.include_related
        freshness = _maybe_refresh_index(config, before="recall", refresh=refresh)
        filters = SearchFilters(
            project=project,
            memory_type=memory_type.value if memory_type else None,
            status=status.value if status else None,
            scope=scope.value if scope else None,
        )
        payload = explain_recall(
            config,
            query,
            filters=SearchFilters.from_mapping(filters.to_dict()),
            budget=selected_budget,
            include_related=selected_include_related,
            semantic=semantic,
            mode=mode,
        ).to_dict()
        payload.update(
            {
                "task_class": selected_task_class,
                "recall_policy": task_policy.model_dump(mode="json"),
                "freshness": freshness,
            }
        )
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "explain_recall_failed",
        )

    if json_output:
        _print_json(payload)
        return

    console.print(
        f"[green]Selected {payload['selected_count']} chunk(s)[/green] "
        f"using {payload['used_tokens_estimate']}/{payload['budget']} estimated tokens"
    )
    if payload["selected"]:
        console.print("[bold]Selected[/bold]")
        for item in payload["selected"]:
            console.print(f"- {item['explanation']} [dim]{item['path']}[/dim]")
    if payload["skipped"]:
        console.print("[bold]Skipped[/bold]")
        for item in payload["skipped"][:8]:
            console.print(f"- {item['explanation']} [dim]{item['path']}[/dim]")


@app.command("lookup-source")
def lookup_source_command(
    source_id: str = typer.Argument(..., help="Source directory id under Sources/."),
    query: Optional[str] = typer.Option(None, "--query", help="Optional query used to rank source chunks."),
    budget: int = typer.Option(800, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Client-controlled recall session id."),
    loaded_source_id: list[str] = typer.Option(
        [],
        "--loaded-source-id",
        help="Source id already loaded in this session; may be repeated or comma-separated.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Return compact read-only evidence for a saved source directory."""

    try:
        config = load_config(vault)
        payload = lookup_source(
            config,
            source_id,
            query=query,
            budget=budget,
            session_id=session_id,
            loaded_source_ids=loaded_source_id,
        )
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="lookup_source_failed")

    if json_output:
        _print_json(payload)
        if not payload.get("ok", False):
            raise typer.Exit(1)
        return

    if not payload.get("ok", False):
        error = payload.get("error") or {}
        code = str(error.get("code") or "lookup_source_failed")
        message = str(error.get("message") or "lookup source failed")
        console.print(f"[red]{code}:[/red] {message}")
        raise typer.Exit(1)

    if not payload["chunks"]:
        reason = payload.get("empty_reason") or "no_chunks"
        console.print(f"[yellow]No source chunks found.[/yellow] reason={reason}")
        return

    source_path = payload.get("source_path") or "-"
    console.print(f"[green]Source chunks:[/green] {payload['source_id']} [dim]{source_path}[/dim]")
    for position, chunk in enumerate(payload["chunks"], start=1):
        citation = chunk["citation"]
        console.print(
            f"[bold]{position}.[/bold] {citation['path']} "
            f"[dim]kind={citation['kind']} tokens={chunk['tokens_estimate']}[/dim]"
        )
        console.print(f"   {chunk['text']}", markup=False, soft_wrap=True)


@app.command()
def brief(
    query: str = typer.Argument(..., help="Brief query."),
    budget: Optional[int] = typer.Option(None, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    task_class: str = typer.Option("default", "--task-class", help="Recall policy class: default, coding, planning, or review."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this brief.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Client-controlled recall session id."),
    loaded_memory_id: list[str] = typer.Option(
        [],
        "--loaded-memory-id",
        help="Memory id already loaded in this session; may be repeated or comma-separated.",
    ),
    loaded_source_id: list[str] = typer.Option(
        [],
        "--loaded-source-id",
        help="Source id already loaded in this session; may be repeated or comma-separated.",
    ),
    refresh: Optional[bool] = typer.Option(
        None,
        "--refresh/--no-refresh",
        help="Refresh the index before brief; defaults to index_freshness config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Generate a citation-preserving memora brief under a strict budget."""

    try:
        config = load_config(vault)
        selected_task_class, task_policy = _resolve_task_policy(config, task_class)
        selected_budget = _cli_budget(budget, task_policy)
        selected_include_related = include_related or task_policy.include_related
        freshness = _maybe_refresh_index(config, before="recall", refresh=refresh)
        filters = SearchFilters(
            project=project,
            memory_type=memory_type.value if memory_type else None,
            status=status.value if status else None,
            scope=scope.value if scope else None,
        )
        payload = brief_memory(
            config,
            query,
            filters=SearchFilters.from_mapping(filters.to_dict()),
            budget=selected_budget,
            include_related=selected_include_related,
            semantic=semantic,
            mode=mode,
            session_id=session_id,
            loaded_memory_ids=loaded_memory_id,
            loaded_source_ids=loaded_source_id,
        ).to_dict()
        payload.update(
            {
                "task_class": selected_task_class,
                "recall_policy": task_policy.model_dump(mode="json"),
                "freshness": freshness,
            }
        )
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "brief_failed",
        )

    if json_output:
        _print_json(payload)
        return

    console.print(payload["markdown"], markup=False, end="", soft_wrap=True)


@app.command("build-context")
def build_context_command(
    task: str = typer.Argument(..., help="User task to enrich with memory when useful."),
    budget: Optional[int] = typer.Option(None, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    task_class: str = typer.Option("default", "--task-class", help="Recall policy class: default, coding, planning, or review."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    include_profile: Optional[bool] = typer.Option(
        None,
        "--include-profile/--no-include-profile",
        help="Override bounded generated profile context inclusion.",
    ),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this context.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Client-controlled recall session id."),
    loaded_memory_id: list[str] = typer.Option(
        [],
        "--loaded-memory-id",
        help="Memory id already loaded in this session; may be repeated or comma-separated.",
    ),
    loaded_source_id: list[str] = typer.Option(
        [],
        "--loaded-source-id",
        help="Source id already loaded in this session; may be repeated or comma-separated.",
    ),
    refresh: Optional[bool] = typer.Option(
        None,
        "--refresh/--no-refresh",
        help="Refresh the index before context building; defaults to index_freshness config.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Build CLI-first agent context with deterministic recall gating."""

    try:
        config = load_config(vault)
        selected_task_class, task_policy = _resolve_task_policy(config, task_class)
        selected_budget = _cli_budget(budget, task_policy)
        profile_requested, profile_request_sources = _resolve_profile_request(
            config,
            task_policy,
            include_profile,
        )
        if not config.agent_policy.enabled or not config.agent_policy.auto_recall:
            decision = {
                "should_recall": False,
                "query": task,
                "confidence": 0.0,
                "trigger_count": 0,
                "triggers": [],
                "reason": "agent memory is disabled" if not config.agent_policy.enabled else "agent auto recall is disabled",
            }
        else:
            decision = should_recall(task, aliases=config.agent_policy.aliases).to_dict()
        if not decision["should_recall"]:
            session_payload = session_trace(
                normalize_session_recall_state(
                    session_id=session_id,
                    loaded_memory_ids=loaded_memory_id,
                    loaded_source_ids=loaded_source_id,
                )
            )
            profile_payload = _policy_skipped_profile_payload(
                config,
                requested=profile_requested,
                request_sources=profile_request_sources,
                project=project,
                task_budget=selected_budget,
            )
            payload = {
                "ok": True,
                "implemented": True,
                "command": "build-context",
                "task": task,
                "memory_needed": False,
                "task_class": selected_task_class,
                "budget": selected_budget,
                "policy": decision,
                "profile": profile_payload,
                "trace": _build_context_trace(
                    decision,
                    task_class=selected_task_class,
                    task_policy=task_policy,
                    profile=profile_payload,
                    task_budget=selected_budget,
                    session=session_payload,
                    empty_reason="policy_skipped",
                ),
                "markdown": "",
                "citations": [],
                "brief": None,
            }
            if session_payload:
                payload["session"] = session_payload
        else:
            freshness = _maybe_refresh_index(config, before="recall", refresh=refresh)
            filters = SearchFilters(project=project)
            profile_payload = build_context_profile_payload(
                config,
                requested=profile_requested,
                request_sources=profile_request_sources,
                project=project,
                task_budget=selected_budget,
            )
            brief_payload = brief_memory(
                config,
                str(decision["query"]),
                filters=SearchFilters.from_mapping(filters.to_dict()),
                budget=selected_budget,
                include_related=include_related or task_policy.include_related,
                semantic=semantic,
                mode=mode,
                session_id=session_id,
                loaded_memory_ids=loaded_memory_id,
                loaded_source_ids=loaded_source_id,
            ).to_dict()
            session_payload = brief_payload.get("session")
            payload = {
                "ok": True,
                "implemented": True,
                "command": "build-context",
                "task": task,
                "memory_needed": True,
                "task_class": selected_task_class,
                "budget": selected_budget,
                "policy": decision,
                "freshness": freshness,
                "recall_policy": task_policy.model_dump(mode="json"),
                "profile": profile_payload,
                "trace": _build_context_trace(
                    decision,
                    task_class=selected_task_class,
                    task_policy=task_policy,
                    profile=profile_payload,
                    task_budget=selected_budget,
                    freshness=freshness,
                    retrieval=brief_payload.get("retrieval"),
                    session=session_payload,
                    selected_count=int(brief_payload.get("recall", {}).get("chunk_count", 0)),
                ),
                "markdown": _compose_context_markdown(profile_payload, brief_payload["markdown"]),
                "citations": [*profile_payload.get("citations", []), *brief_payload["citations"]],
                "brief": brief_payload,
            }
            if session_payload:
                payload["session"] = session_payload
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="index_missing" if isinstance(exc, RetrievalIndexError) else "build_context_failed",
        )

    if json_output:
        _print_json(payload)
        return

    if not payload["memory_needed"]:
        console.print("[yellow]No memory context needed.[/yellow]")
        return
    console.print(payload["markdown"], markup=False, end="", soft_wrap=True)


@app.command("synthesize", hidden=True)
def synthesize_command(
    query: Optional[str] = typer.Argument(None, help="Optional topic/query filter."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    title: Optional[str] = typer.Option(None, "--title", help="Synthesis title."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum active memories to include."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the generated synthesis without writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Plan or write a deterministic generated synthesis Markdown file."""

    try:
        config = load_config(vault)
        synthesize = plan_synthesis if dry_run else write_synthesis
        payload = synthesize(
            config,
            project=project,
            query=query,
            title=title,
            limit=limit,
        ).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="synthesize_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["dry_run"]:
        console.print("[green]Synthesis dry run:[/green] no files written")
        console.print(f"Planned path: {payload['relative_path']}")
        console.print(payload["markdown"], markup=False, end="", soft_wrap=True)
        return

    console.print(f"[green]Wrote synthesis:[/green] {payload['relative_path']}")
    console.print(f"Memories: {payload['memory_count']}")


@app.command("should-recall", hidden=True)
def should_recall_command(
    message: str = typer.Argument(..., help="User message to classify."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Decide whether a user request should be enriched with memory."""

    payload = should_recall(message).to_dict()
    if json_output:
        _print_json(payload)
        return

    if payload["should_recall"]:
        console.print(
            f"[green]Recall recommended[/green] "
            f"(confidence={payload['confidence']:.2f}, triggers={payload['trigger_count']})"
        )
        for trigger in payload["triggers"]:
            console.print(f"- {trigger['name']}: {trigger['description']}")
        return

    console.print(f"[yellow]No memory needed[/yellow] (confidence={payload['confidence']:.2f})")


@app.command()
def status(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Summarize vault health and local index state."""

    try:
        config = load_config(vault)
        payload = status_summary(config)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="status_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"Vault: {payload['vault_path']}")
    console.print(f"Memories: {payload['memory_count']}")
    console.print(f"Pending: {payload['pending_count']}")
    console.print(f"Issues: {payload['issue_count']}")
    console.print(f"Index exists: {payload['index_exists']}")


@app.command()
def inspect(
    memory_id: str = typer.Argument(..., help="Memory id to inspect."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Inspect one memory by id."""

    try:
        config = load_config(vault)
        payload = inspect_memory(config, memory_id)
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="memory_not_found" if isinstance(exc, ValueError) else "inspect_failed",
        )

    if json_output:
        _print_json(payload)
        return

    memory = payload["memory"]
    console.print(f"[bold]{payload['id']}[/bold] [dim]{payload['relative_path']}[/dim]")
    console.print(
        f"{memory['type']} / {memory['status']} / "
        f"scope={memory['scope']} / project={memory.get('project') or '-'}"
    )
    console.print(f"Path: {payload['path']}")
    console.print(f"Obsidian: {payload['obsidian_uri']}")
    if memory.get("source"):
        console.print(f"Source: {memory['source']}")
    if payload["body"]:
        console.print("")
        console.print(payload["body"], markup=False, soft_wrap=True)


@app.command("open")
def open_command(
    memory_id: str = typer.Argument(..., help="Memory id to locate."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    launch: bool = typer.Option(False, "--launch", help="Open the Obsidian URI with the system `open` command."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Print the Markdown path and Obsidian URI for a memory."""

    try:
        config = load_config(vault)
        payload = open_memory(config, memory_id, launch=launch)
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="memory_not_found" if isinstance(exc, ValueError) else "open_failed",
        )

    if json_output:
        _print_json(payload)
        return

    console.print(f"Path: {payload['path']}")
    console.print(f"Obsidian: {payload['obsidian_uri']}")
    if payload["launch_requested"]:
        if payload["opened"]:
            console.print("[green]Opened with system handler.[/green]")
        else:
            console.print(f"[yellow]Launch failed:[/yellow] {payload['launch_error']}")


@app.command(hidden=True)
def graph(
    memory_id: str = typer.Argument(..., help="Memory id to graph."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show incoming and outgoing graph links for a memory."""

    try:
        config = load_config(vault)
        payload = graph_memory(config, memory_id)
    except Exception as exc:
        _handle_error(
            exc,
            json_output=json_output,
            code="memory_not_found" if isinstance(exc, ValueError) else "graph_failed",
        )

    if json_output:
        _print_json(payload)
        return

    memory = payload["memory"]
    console.print(f"[bold]{memory['id']}[/bold] [dim]{memory['path']}[/dim]")
    console.print(
        f"{memory['type']} / {memory['status']} / "
        f"links={payload['link_count']} (out={len(payload['outgoing'])}, in={len(payload['incoming'])})"
    )
    _print_graph_links("Outgoing", payload["outgoing"])
    _print_graph_links("Incoming", payload["incoming"])


@app.command()
def doctor(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Validate Stage 1 memory Markdown schema."""

    try:
        config = load_config(vault)
        payload = doctor_report(config)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="doctor_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["ok"]:
        console.print(f"[green]Doctor passed[/green]: {payload['documents']} memory files validated.")
        if payload.get("warning_count"):
            console.print(f"[yellow]Warnings:[/yellow] {payload['warning_count']}")
            for warning in payload.get("warnings", []):
                console.print(f"- {warning['path']}: {warning['message']}")
    else:
        console.print(f"[red]Doctor found {len(payload['issues'])} issue(s).[/red]")
        for issue in payload["issues"]:
            console.print(f"- {issue['path']}: {issue['message']}")
        for warning in payload.get("warnings", []):
            console.print(f"- {warning['path']}: {warning['message']}")
        raise typer.Exit(1)


@app.command(hidden=True)
def conflicts(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Detect Markdown sync conflicts that require manual resolution."""

    try:
        config = load_config(vault)
        payload = detect_sync_conflicts(config).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="conflicts_failed")

    if json_output:
        _print_json(payload)
        if not payload["ok"]:
            raise typer.Exit(1)
        return

    if payload["ok"]:
        console.print("[green]No Markdown sync conflicts found.[/green]")
        return

    console.print(f"[red]Found {payload['conflict_count']} Markdown sync conflict(s).[/red]")
    for issue in payload["issues"]:
        location = issue["path"]
        if "line" in issue:
            location = f"{location}:{issue['line']}"
        console.print(f"- {issue['kind']} at {location}: {issue['message']}")
        if issue.get("paths"):
            console.print(f"  paths: {', '.join(issue['paths'])}")
    raise typer.Exit(1)


@app.command(hidden=True)
def mark(
    memory_id: str = typer.Argument(..., help="Memory id to update."),
    status: LifecycleStatus = typer.Option(..., "--status", help="Lifecycle status to set."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional audit reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Set a memory lifecycle status."""

    try:
        config = load_config(vault)
        payload = mark_status(config, memory_id, status, reason=reason).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="mark_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["mutation_count"] == 0:
        console.print(f"[yellow]{payload['id']} already has status {payload['status']}.[/yellow]")
        return
    mutation = payload["mutations"][0]
    console.print(
        f"[green]Updated memory:[/green] {mutation['id']} "
        f"{mutation['previous_status']} -> {mutation['status']}"
    )


@app.command(hidden=True)
def reject(
    memory_id: str = typer.Argument(..., help="Memory id to reject."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional audit reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Reject a memory so default retrieval excludes it."""

    try:
        config = load_config(vault)
        payload = reject_memory(config, memory_id, reason=reason).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="reject_failed")

    if json_output:
        _print_json(payload)
        return

    if payload["mutation_count"] == 0:
        console.print(f"[yellow]{payload['id']} already has status rejected.[/yellow]")
        return
    console.print(f"[green]Rejected memory:[/green] {memory_id}")


@app.command(hidden=True)
def supersede(
    old_id: str = typer.Argument(..., help="Superseded memory id."),
    by_id: str = typer.Option(..., "--by", help="Replacement memory id."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional audit reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Mark one memory superseded by another and create the graph relation."""

    try:
        config = load_config(vault)
        payload = supersede_memory(config, old_id, new_id=by_id, reason=reason).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="supersede_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Superseded memory:[/green] {old_id} by {by_id}")


@app.command(hidden=True)
def contradict(
    id1: str = typer.Argument(..., help="First memory id."),
    id2: str = typer.Argument(..., help="Second memory id."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional audit reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Record a contradiction between two memories."""

    try:
        config = load_config(vault)
        payload = contradict_memories(config, id1, id2, reason=reason).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="contradict_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Recorded contradiction:[/green] {id1} contradicts {id2}")


@app.command(hidden=True)
def decay(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Mark active memories with elapsed valid_to dates as stale."""

    try:
        config = load_config(vault)
        payload = decay_memories(config).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="decay_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Decay complete:[/green] {payload['changed']} memory file(s) marked stale")


@review_app.callback(invoke_without_command=True)
def review(
    ctx: typer.Context,
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    group_by: Optional[str] = typer.Option(None, "--group-by", help="Group human output by: source."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List pending agent-generated memories awaiting review."""

    if ctx.invoked_subcommand is not None:
        return

    try:
        group_by = _normalize_review_group_by(group_by)
        config = load_config(vault)
        payload = review_queue(config).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="review_failed")

    if json_output:
        _print_json(payload)
        return

    if not payload["items"]:
        console.print("[green]No pending agent memories.[/green]")
        return
    console.print(f"[yellow]Pending agent memories:[/yellow] {payload['pending_count']}")
    if group_by == "source":
        for group in payload["source_groups"]:
            _print_review_source_group(group)
        return
    for item in payload["items"]:
        _print_review_diff(item)


@review_app.command("approve")
def review_approve(
    memory_ids: list[str] = typer.Argument(..., help="Pending memory ids to approve."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Audit reason for approval."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview approvals without writing files."),
    override_unsafe: bool = typer.Option(
        False,
        "--override-unsafe",
        help="Approve items with unsafe recall risk flags after explicit review.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Approve pending agent-generated memories by id."""

    _review_action_command(
        "approve",
        memory_ids,
        vault=vault,
        reason=reason,
        dry_run=dry_run,
        override_unsafe=override_unsafe,
        json_output=json_output,
    )


@review_app.command("reject")
def review_reject(
    memory_ids: list[str] = typer.Argument(..., help="Pending memory ids to reject."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Audit reason for rejection."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview rejections without writing files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Reject pending agent-generated memories by id."""

    _review_action_command(
        "reject",
        memory_ids,
        vault=vault,
        reason=reason,
        dry_run=dry_run,
        json_output=json_output,
    )


@review_app.command("defer", hidden=True)
def review_defer(
    memory_ids: list[str] = typer.Argument(..., help="Pending memory ids to leave pending."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Audit reason for deferral."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview deferrals without writing files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Defer pending agent-generated memories by keeping them pending."""

    _review_action_command(
        "defer",
        memory_ids,
        vault=vault,
        reason=reason,
        dry_run=dry_run,
        json_output=json_output,
    )


@review_app.command("supersede", hidden=True)
def review_supersede(
    old_id: str = typer.Argument(..., help="Pending memory id to supersede."),
    by_id: str = typer.Option(..., "--by", help="Replacement memory id."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Audit reason for superseding."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview supersede without writing files."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Supersede one pending memory by a replacement memory."""

    _review_action_command(
        "supersede",
        [old_id],
        vault=vault,
        reason=reason,
        dry_run=dry_run,
        by_id=by_id,
        json_output=json_output,
    )


@app.command(hidden=True)
def curate(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    source: Optional[str] = typer.Option(None, "--source", help="Source path, URL, or source_id filter."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Propose conservative review actions without changing memory files."""

    try:
        config = load_config(vault)
        payload = curation_plan(config, project=project, source=source)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="curate_failed")

    if json_output:
        _print_json(payload)
        return

    if not payload["items"]:
        console.print("[green]No pending agent memories to curate.[/green]")
        return
    console.print(
        f"[yellow]Curation proposals:[/yellow] "
        f"{payload['proposal_count']} of {payload['pending_count']} pending item(s)"
    )
    for item in payload["items"]:
        _print_curation_proposal(item)


@app.command("eval", hidden=True)
def eval_command(
    fixture_or_file: Path = typer.Argument(..., help="Evaluation fixture directory or YAML spec."),
    keep_working_vault: bool = typer.Option(
        False,
        "--keep-working-vault",
        help="Keep the throwaway copied vault for debugging.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Run deterministic fixture-backed recall/search/brief evaluation cases."""

    try:
        payload = run_evaluation(fixture_or_file, keep_working_vault=keep_working_vault).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="eval_failed")

    if json_output:
        _print_json(payload)
        if not payload["ok"]:
            raise typer.Exit(1)
        return

    status_label = "[green]passed[/green]" if payload["ok"] else "[red]failed[/red]"
    console.print(
        f"Evaluation {status_label}: "
        f"{payload['case_count'] - payload['failed_count']}/{payload['case_count']} cases passed"
    )
    if not payload["ok"]:
        for case in payload["cases"]:
            if case["passed"]:
                continue
            console.print(
                f"- {case['id']}: missing={case['missing_ids']} "
                f"unexpected={case['unexpected_ids']} warnings={case['missing_warnings']}"
            )
        raise typer.Exit(1)


@app.command("import", hidden=True)
def import_command(
    path: Path = typer.Argument(..., help="Path to import later."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Placeholder for Markdown and Basic Memory-compatible import."""

    _placeholder_command("import", vault=vault, json_output=json_output, path=str(path))


@app.command("export", hidden=True)
def export_command(
    export_format: str = typer.Option("markdown", "--format", help="Export format."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Placeholder for Markdown export."""

    _placeholder_command("export", vault=vault, json_output=json_output, format=export_format)


def _print_graph_links(title: str, links: list[dict[str, Any]]) -> None:
    console.print(f"[bold]{title}[/bold] ({len(links)})")
    if not links:
        console.print("  [dim]none[/dim]")
        return
    for link in links:
        other = link.get("other") or {}
        other_id = other.get("id") or (link["to_id"] if link["direction"] == "outgoing" else link["from_id"])
        path = other.get("path") or "-"
        console.print(f"  - {link['relation']}: {other_id} [dim]{path}[/dim]")


def _review_action_command(
    action: str,
    memory_ids: list[str],
    *,
    vault: Optional[Path],
    reason: Optional[str],
    dry_run: bool,
    json_output: bool,
    override_unsafe: bool = False,
    by_id: Optional[str] = None,
) -> None:
    try:
        config = load_config(vault)
        payload = review_batch_action(
            config,
            action,
            memory_ids,
            reason=reason,
            dry_run=dry_run,
            override_unsafe=override_unsafe,
            by_id=by_id,
        ).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code=f"review_{action}_failed")

    if json_output:
        _print_json(payload)
        if not payload["ok"]:
            raise typer.Exit(1)
        return

    _print_review_action_result(payload)
    if not payload["ok"]:
        raise typer.Exit(1)


def _print_review_action_result(payload: dict[str, Any]) -> None:
    action = payload["action"]
    label = "Planned" if payload["dry_run"] else "Applied"
    console.print(
        f"[green]{label} review {action}:[/green] "
        f"{payload['success_count']} succeeded, {payload['failure_count']} failed"
    )
    for result in payload["results"]:
        if result["ok"]:
            status = result.get("status") or "-"
            previous = result.get("previous_status") or "-"
            marker = "would update" if payload["dry_run"] else "updated"
            console.print(f"- {marker} {result['id']}: {previous} -> {status}")
            continue
        error = result.get("error") or {}
        code = error.get("code") or "review_action_failed"
        message = error.get("message") or "review action failed"
        console.print(f"- [red]{result['id']}[/red]: {code}: {message}")


def _print_review_diff(item: dict[str, Any]) -> None:
    console.print("")
    console.print(f"[dim]diff -- memory/{item['id']} {item['relative_path']}[/dim]")
    console.print(f"[bold]{item['id']}[/bold] [dim]{item['relative_path']}[/dim]")
    console.print(
        f"type={item['type']} status={item['status']} "
        f"confidence={item['confidence']} recommended={item.get('recommended_action', 'inspect')}"
    )
    console.print(f"source: {_format_source(item.get('source'))}")
    risk_flags = item.get("risk_flags") or []
    if risk_flags:
        console.print(f"risk: {', '.join(risk_flags)}")
    actions = item.get("proposed_actions") or ["approve", "reject", "defer", "inspect"]
    console.print(f"actions: {', '.join(actions)}")
    for line in (
        f"+ id: {item['id']}",
        f"+ type: {item['type']}",
        f"+ status: {item['status']}",
        f"+ confidence: {item['confidence']}",
        f"+ source: {_format_source(item.get('source'))}",
    ):
        console.print(line, markup=False)
    console.print("body:", markup=False)
    body = item.get("body") or ""
    for line in body.splitlines() or [""]:
        console.print(f"+ {line}", markup=False)


def _print_review_source_group(group: dict[str, Any]) -> None:
    console.print("")
    console.print(
        f"[bold]Source: {_format_source(group.get('source'))}[/bold] "
        f"[dim]({group['item_count']} pending)[/dim]"
    )
    for item in group["items"]:
        _print_review_diff(item)


def _print_curation_proposal(item: dict[str, Any]) -> None:
    console.print(
        f"- [bold]{item['id']}[/bold]: {item['recommended_action']} "
        f"[dim]{item['type']}/{item['status']} {item['relative_path']}[/dim]"
    )
    risk_flags = item.get("risk_flags") or []
    if risk_flags:
        console.print(f"  risks: {', '.join(risk_flags)}")
    summaries = item.get("candidate_summaries") or []
    if summaries:
        formatted = [
            f"{summary['kind']}:{summary['id']}"
            for summary in summaries
        ]
        console.print(f"  candidates: {', '.join(formatted)}")


def _format_source(source: Any) -> str:
    if not source:
        return "-"
    if isinstance(source, dict):
        if source.get("path"):
            return str(source["path"])
        if source.get("url"):
            return str(source["url"])
    return str(source)


def _normalize_review_group_by(group_by: Optional[str]) -> Optional[str]:
    if group_by is None:
        return None
    normalized = group_by.strip().lower()
    if normalized == "source":
        return normalized
    raise ValueError(f"unsupported --group-by value {group_by!r}; expected 'source'")


def _placeholder_command(
    command: str,
    *,
    vault: Optional[Path],
    json_output: bool,
    **details: Any,
) -> None:
    try:
        config = load_config(vault)
        payload = placeholder_result(command, vault_path=str(config.vault_path), **details)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code=f"{command}_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[yellow]{payload['message']}[/yellow]")


def _source_inbox_files(path: Path) -> list[Path]:
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in SOURCE_INBOX_SUFFIXES
    )


def _source_inbox_plan_payload(path: Path, candidates: list[Path], *, dry_run: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "implemented": True,
        "command": "import-source-inbox",
        "dry_run": dry_run,
        "inbox_path": str(path),
        "source_count": len(candidates),
        "sources": [
            {
                "path": str(candidate),
                "title": candidate.stem,
                "suffix": candidate.suffix.lower(),
            }
            for candidate in candidates
        ],
    }


def _source_inbox_scan_payload(
    config: Any,
    *,
    path: Optional[Path],
    project: Optional[str],
    sensitivity: Optional[str],
    tags: list[str],
    limit: Optional[int],
    dry_run: bool,
    ignore_disabled: bool,
) -> dict[str, Any]:
    connector_config = config.connectors.source_inbox
    inbox = _resolve_source_inbox_scan_path(config, path)
    if not inbox.is_dir():
        raise ValueError(f"source inbox is not a directory: {inbox}")

    selected_sensitivity = sensitivity or connector_config.sensitivity
    files = sorted(item for item in inbox.rglob("*") if item.is_file())
    planned: list[dict[str, Any]] = []
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    processed = 0

    for candidate in files:
        file_payload = _source_inbox_scan_file_payload(config, inbox, candidate)
        if not _source_inbox_matches_patterns(candidate, inbox=inbox, patterns=connector_config.patterns):
            skipped.append({**file_payload, "reason": "pattern_not_matched"})
            continue

        route = _source_inbox_route(candidate, inbox=inbox)
        if route is None:
            skipped.append({**file_payload, "reason": "unsupported_file_type"})
            continue

        disabled = _source_inbox_disabled_connectors(config, route["connector"])
        if disabled and not ignore_disabled:
            skipped.append(
                {
                    **file_payload,
                    "reason": "connector_disabled",
                    "connector": route["connector"],
                    "disabled_connectors": disabled,
                }
            )
            continue

        if limit is not None and processed >= limit:
            skipped.append({**file_payload, "reason": "limit_reached", "connector": route["connector"]})
            continue

        processed += 1
        try:
            if dry_run:
                planned.append(
                    _source_inbox_scan_plan(
                        config,
                        candidate,
                        file_payload=file_payload,
                        route=route,
                        project=project,
                        sensitivity=selected_sensitivity,
                        tags=tags,
                    )
                )
            else:
                imported.append(
                    _source_inbox_scan_import(
                        config,
                        candidate,
                        file_payload=file_payload,
                        route=route,
                        project=project,
                        sensitivity=selected_sensitivity,
                        tags=tags,
                    )
                )
        except Exception as exc:
            errors.append(
                {
                    **file_payload,
                    "connector": route["connector"],
                    "command": route["command"],
                    "code": "source_inbox_item_failed",
                    "message": str(exc),
                }
            )

    return {
        "ok": not errors,
        "implemented": True,
        "command": "source-inbox scan",
        "dry_run": dry_run,
        "vault_path": str(config.vault_path),
        "inbox_path": str(inbox),
        "patterns": list(connector_config.patterns),
        "ignore_disabled": ignore_disabled,
        "connectors": config.connectors.model_dump(mode="json"),
        "file_count": len(files),
        "planned_count": len(planned),
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "planned": planned,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "next_steps": _source_inbox_scan_next_steps(dry_run=dry_run, ignore_disabled=ignore_disabled),
    }


def _resolve_source_inbox_scan_path(config: Any, path: Optional[Path]) -> Path:
    if path is not None:
        candidate = path.expanduser()
        return candidate.resolve() if candidate.is_absolute() else candidate.resolve()
    configured = Path(config.connectors.source_inbox.path).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (config.vault_path / configured).resolve()


def _source_inbox_scan_file_payload(config: Any, inbox: Path, path: Path) -> dict[str, Any]:
    try:
        inbox_relative = path.relative_to(inbox).as_posix()
    except ValueError:
        inbox_relative = path.name
    return {
        "path": str(path),
        "relative_path": _relative_to_vault(config, path),
        "inbox_relative_path": inbox_relative,
        "file_name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "content_hash": _file_content_hash(path),
    }


def _source_inbox_matches_patterns(path: Path, *, inbox: Path, patterns: list[str]) -> bool:
    relative = path.relative_to(inbox).as_posix()
    return any(
        pattern in {"*", "**", "**/*"} or fnmatch(relative, pattern) or fnmatch(path.name, pattern)
        for pattern in patterns
    )


def _source_inbox_route(path: Path, *, inbox: Path) -> Optional[dict[str, str]]:
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.relative_to(inbox).parts[:-1]}
    parts.add(path.parent.name.lower())
    if suffix == ".pdf":
        return {"connector": "pdf", "command": "import-pdf"}
    if "slack" in parts and suffix in {*SOURCE_INBOX_SCAN_TEXT_SUFFIXES, ".json"}:
        return {"connector": "slack", "command": "import-slack"}
    if "zoom" in parts and suffix in SOURCE_INBOX_SCAN_TEXT_SUFFIXES:
        return {"connector": "zoom", "command": "import-zoom"}
    if suffix in SOURCE_INBOX_SCAN_TEXT_SUFFIXES:
        return {"connector": "source_inbox", "command": "import-source"}
    if suffix in SOURCE_INBOX_SCAN_SUPPORTED_SUFFIXES:
        return None
    return None


def _source_inbox_disabled_connectors(config: Any, connector: str) -> list[str]:
    disabled: list[str] = []
    if not config.connectors.source_inbox.enabled:
        disabled.append("source_inbox")
    if connector != "source_inbox" and not getattr(config.connectors, connector).enabled:
        disabled.append(connector)
    return disabled


def _source_inbox_scan_plan(
    config: Any,
    path: Path,
    *,
    file_payload: dict[str, Any],
    route: dict[str, str],
    project: Optional[str],
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    connector = route["connector"]
    if connector == "pdf":
        payload = _pdf_import_plan_payload(
            config,
            path=path,
            title=None,
            text_file=None,
            project=project,
            channel="pdf",
            source_quality="user_provided",
            sensitivity=sensitivity,
            tags=tags,
        )
    elif connector == "zoom":
        imported = load_zoom_content(path)
        payload = _zoom_import_plan_payload(
            config,
            imported=imported,
            project=project,
            channel="zoom",
            source_quality="meeting_summary",
            sensitivity=sensitivity,
            tags=tags,
        )
    elif connector == "slack":
        imported = load_slack_content(path)
        payload = _slack_import_plan_payload(
            config,
            imported=imported,
            project=project,
            source_quality="chat_thread",
            sensitivity=sensitivity,
            tags=tags,
        )
    else:
        payload = {
            "ok": True,
            "implemented": True,
            "command": "import-source",
            "dry_run": True,
            "vault_path": str(config.vault_path),
            "path": str(path),
            "title": path.stem,
            "project": project,
            "tags": tags,
            "channel": "source_inbox",
            "source_quality": config.connectors.source_inbox.source_quality,
            "sensitivity": sensitivity,
            "origin": _source_inbox_origin(file_payload, provider="source_inbox"),
            "would_read_file": str(path),
            "would_write": "Sources/<source_id>/source.md",
        }
    return {
        **file_payload,
        "connector": connector,
        "command": route["command"],
        "dry_run": True,
        "plan": payload,
    }


def _source_inbox_scan_import(
    config: Any,
    path: Path,
    *,
    file_payload: dict[str, Any],
    route: dict[str, str],
    project: Optional[str],
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    connector = route["connector"]
    if connector == "pdf":
        imported = load_pdf_content(path)
        result = save_source_material(
            config,
            title=imported.title,
            content=imported.content,
            extract=imported.extract,
            project=project,
            tags=tags,
            channel="pdf",
            source_quality="user_provided",
            sensitivity=sensitivity,
            origin={**imported.origin, **_source_inbox_origin(file_payload)},
        ).to_dict()
        result.update({"content": imported.summary()})
    elif connector == "zoom":
        imported = load_zoom_content(path)
        result = save_source_material(
            config,
            title=imported.title,
            url=_meeting_url(imported.meeting),
            content=imported.content,
            extract=imported.extract,
            project=project,
            tags=tags,
            channel="zoom",
            source_quality="meeting_summary",
            sensitivity=sensitivity,
            origin={**imported.origin, **_source_inbox_origin(file_payload)},
        ).to_dict()
        result.update({"content": imported.summary(), "meeting": imported.meeting})
    elif connector == "slack":
        imported = load_slack_content(path)
        result = save_source_material(
            config,
            title=imported.title,
            url=_thread_permalink(imported.thread),
            content=imported.content,
            extract=imported.extract,
            project=project,
            tags=tags,
            channel="slack",
            source_quality="chat_thread",
            sensitivity=sensitivity,
            origin={**imported.origin, **_source_inbox_origin(file_payload)},
        ).to_dict()
        result.update({"content": imported.summary(), "thread": imported.thread})
    else:
        result = save_source_material(
            config,
            title=path.stem,
            content=path.read_text(encoding="utf-8"),
            project=project,
            tags=tags,
            channel="source_inbox",
            source_quality=config.connectors.source_inbox.source_quality,
            sensitivity=sensitivity,
            origin=_source_inbox_origin(file_payload, provider="source_inbox"),
        ).to_dict()
    result.update(
        {
            **file_payload,
            "connector": connector,
            "command": route["command"],
            "dry_run": False,
        }
    )
    return result


def _source_inbox_origin(file_payload: Mapping[str, Any], *, provider: Optional[str] = None) -> dict[str, str]:
    origin = {
        "source_inbox_path": str(file_payload["path"]),
        "source_inbox_relative_path": str(file_payload["inbox_relative_path"]),
        "file_name": str(file_payload["file_name"]),
        "content_hash": str(file_payload["content_hash"]),
    }
    if provider is not None:
        origin["provider"] = provider
    return origin


def _source_inbox_scan_next_steps(*, dry_run: bool, ignore_disabled: bool) -> list[str]:
    steps = [
        "Review saved Sources/ material before creating source-backed memories.",
        "Keep inferred agent-created memories pending for explicit review.",
    ]
    if dry_run:
        steps.insert(0, "Run without --dry-run to save planned local sources.")
    if ignore_disabled:
        steps.append("Consider enabling only the connectors you intend to scan in `.memora/config.yaml`.")
    return steps


def _url_import_plan_payload(
    config: Any,
    *,
    url: str,
    title: Optional[str],
    from_file: Optional[Path],
    project: Optional[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    fetcher = "from_file" if from_file is not None else "stdlib"
    origin: dict[str, Any] = {
        "provider": "url",
        "fetcher": fetcher,
        "url": url,
    }
    if from_file is not None:
        origin["content_file"] = str(from_file.expanduser())
        origin["file_name"] = from_file.name
    return {
        "ok": True,
        "implemented": True,
        "command": "import-url",
        "dry_run": True,
        "vault_path": str(config.vault_path),
        "url": url,
        "title": title,
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": origin,
        "would_fetch": from_file is None,
        "would_read_file": str(from_file.expanduser()) if from_file is not None else None,
        "would_write": "Sources/<source_id>/{source.md,extract.md}",
        "next_steps": [
            "Run without --dry-run to fetch or read the URL content and save it under Sources/.",
            "Review the saved extract before creating pending source-backed memories.",
        ],
    }


def _pdf_import_plan_payload(
    config: Any,
    *,
    path: Path,
    title: Optional[str],
    text_file: Optional[Path],
    project: Optional[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    pdf_path = path.expanduser()
    if not pdf_path.is_file():
        raise ValueError(f"PDF file not found: {pdf_path}")
    origin: dict[str, Any] = {
        "provider": "pdf",
        "path": str(pdf_path),
        "file_name": pdf_path.name,
        "extractor": "text_file" if text_file is not None else "pypdf",
        "source_kind": "pre_extracted_text" if text_file is not None else "pdf_text",
        "content_type": "application/pdf",
    }
    if text_file is not None:
        selected_text_file = text_file.expanduser()
        origin["text_file"] = str(selected_text_file)
        origin["text_file_name"] = selected_text_file.name
    planned_source = {
        "title": title or pdf_path.stem,
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": origin,
    }
    return {
        "ok": True,
        "implemented": True,
        "command": "import-pdf",
        "dry_run": True,
        "vault_path": str(config.vault_path),
        "path": str(pdf_path),
        "title": title or pdf_path.stem,
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": origin,
        "planned_source": planned_source,
        "would_extract": text_file is None,
        "would_read_text_file": str(text_file.expanduser()) if text_file is not None else None,
        "would_write": "Sources/<source_id>/{source.md,extract.md}",
        "next_steps": [
            "Run without --dry-run to extract or read PDF text and save it under Sources/.",
            "Review the saved extract before creating pending source-backed memories.",
        ],
    }


def _zoom_import_plan_payload(
    config: Any,
    *,
    imported: Any,
    project: Optional[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    safety = scan_source_material(
        content=imported.content,
        extract=imported.extract,
        metadata={
            "channel": channel,
            "source_quality": source_quality,
            "sensitivity": sensitivity,
            **imported.origin,
        },
    )
    planned_source = {
        "title": imported.title,
        "url": _meeting_url(imported.meeting),
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": dict(imported.origin),
        "meeting": dict(imported.meeting),
        "risk_flags": list(safety.risk_flags),
        "safety": safety.to_dict(),
    }
    return {
        "ok": True,
        "implemented": True,
        "command": "import-zoom",
        "dry_run": True,
        "vault_path": str(config.vault_path),
        "path": str(imported.path),
        "title": imported.title,
        "url": _meeting_url(imported.meeting),
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": dict(imported.origin),
        "meeting": dict(imported.meeting),
        "content": imported.summary(),
        "risk_flags": list(safety.risk_flags),
        "safety": safety.to_dict(),
        "planned_source": planned_source,
        "would_read_file": str(imported.path),
        "would_write": "Sources/<source_id>/{source.md,extract.md}",
        "next_steps": [
            "Run without --dry-run to save the Zoom export under Sources/.",
            "Review the saved extract before creating pending source-backed memories.",
        ],
    }


def _slack_import_plan_payload(
    config: Any,
    *,
    imported: Any,
    project: Optional[str],
    source_quality: str,
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    safety = scan_source_material(
        content=imported.content,
        extract=imported.extract,
        metadata={
            "channel": "slack",
            "source_quality": source_quality,
            "sensitivity": sensitivity,
            **imported.origin,
        },
    )
    planned_source = {
        "title": imported.title,
        "url": _thread_permalink(imported.thread),
        "project": project,
        "tags": tags,
        "channel": "slack",
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": dict(imported.origin),
        "thread": dict(imported.thread),
        "risk_flags": list(safety.risk_flags),
        "safety": safety.to_dict(),
    }
    return {
        "ok": True,
        "implemented": True,
        "command": "import-slack",
        "dry_run": True,
        "vault_path": str(config.vault_path),
        "path": str(imported.path),
        "title": imported.title,
        "url": _thread_permalink(imported.thread),
        "project": project,
        "tags": tags,
        "channel": "slack",
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": dict(imported.origin),
        "thread": dict(imported.thread),
        "content": imported.summary(),
        "risk_flags": list(safety.risk_flags),
        "safety": safety.to_dict(),
        "planned_source": planned_source,
        "would_read_file": str(imported.path),
        "would_write": "Sources/<source_id>/{source.md,extract.md}",
        "next_steps": [
            "Run without --dry-run to save the Slack export under Sources/.",
            "Review the saved extract before creating pending source-backed memories.",
        ],
    }


def _meeting_url(meeting: Mapping[str, object]) -> Optional[str]:
    value = meeting.get("meeting_url")
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _thread_permalink(thread: Mapping[str, object]) -> Optional[str]:
    value = thread.get("permalink")
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _resolve_raw_path(config: Any, path: Optional[Path]) -> Path:
    if path is None:
        return config.raw_root
    candidate = path.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    parts = candidate.parts
    if parts and parts[0] == config.raw_dir:
        return (config.vault_path / candidate).resolve()
    return (config.raw_root / candidate).resolve()


def _raw_files(path: Path) -> list[Path]:
    if not path.exists():
        raise ValueError(f"raw path not found: {path}")
    if path.is_file():
        return [] if _is_raw_metadata_path(path) else [path]
    return sorted(item for item in path.rglob("*") if item.is_file() and not _is_raw_metadata_path(item))


def _is_processable_raw(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_INBOX_SUFFIXES


def _is_raw_metadata_path(path: Path) -> bool:
    return path.name.endswith(".meta.json")


def _raw_file_payload(config: Any, path: Path, *, include_preview: bool) -> dict[str, Any]:
    content_hash = _file_content_hash(path)
    processable = _is_processable_raw(path)
    metadata = _raw_metadata(path)
    payload: dict[str, Any] = {
        "path": str(path),
        "relative_path": _relative_to_vault(config, path),
        "file_name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "content_hash": content_hash,
        "idempotency_key": f"raw:{_relative_to_vault(config, path)}#{content_hash}",
        "processable": processable,
        "metadata": metadata,
    }
    if include_preview and _is_readable_raw(path):
        payload["preview"] = _read_text_file(path)[:4000]
    return payload


def _raw_metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.meta.json")


def _raw_metadata(path: Path) -> Optional[dict[str, Any]]:
    metadata_path = _raw_metadata_path(path)
    if not metadata_path.is_file():
        return None
    try:
        payload = json_module.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json_module.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_readable_raw(path: Path) -> bool:
    metadata = _raw_metadata(path) or {}
    raw_format = str(metadata.get("format") or "").lower()
    return path.suffix.lower() in RAW_READABLE_SUFFIXES or raw_format in {"markdown", "json", "txt"}


def _normalize_raw_kind(value: str) -> str:
    selected = value.strip().lower()
    if selected not in RAW_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(RAW_KINDS))}")
    return selected


def _normalize_raw_format(value: str) -> str:
    selected = value.strip().lower()
    if selected not in RAW_FORMATS:
        raise ValueError(f"format must be one of: {', '.join(sorted(RAW_FORMATS))}")
    return selected


def _source_channel_for_kind(kind: str) -> str:
    if kind == "text":
        return "file"
    return kind


def _slugify_path_stem(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    slug = "-".join(part for part in cleaned.split("-") if part)
    return slug[:64] or "raw"


def _unique_path(path: Path) -> Path:
    if not path.exists() and not _raw_metadata_path(path).exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists() and not _raw_metadata_path(candidate).exists():
            return candidate
    raise ValueError(f"could not allocate unique raw path for {path.name}")


def _raw_add_payload(
    config: Any,
    path: Path,
    *,
    kind: str,
    source_format: str,
    title: Optional[str],
    project: Optional[str],
    sensitivity: str,
    tags: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    source_path = path.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"raw source file not found: {source_path}")
    selected_kind = _normalize_raw_kind(kind)
    selected_format = _normalize_raw_format(source_format)
    now = datetime.now(timezone.utc).astimezone()
    raw_id = f"{now:%Y-%m-%d_%H%M%S}_{_slugify_path_stem(source_path.stem)}"
    target_dir = config.raw_root / "inbox" / selected_kind
    target_path = _unique_path(target_dir / source_path.name)
    metadata_path = _raw_metadata_path(target_path)
    metadata = {
        "raw_id": raw_id,
        "kind": selected_kind,
        "format": selected_format,
        "title": title or source_path.stem,
        "project": project,
        "tags": list(tags),
        "sensitivity": sensitivity,
        "captured_at": now.isoformat(),
        "original_path": str(source_path),
        "file_name": source_path.name,
        "size_bytes": source_path.stat().st_size,
        "content_hash": _file_content_hash(source_path),
    }
    payload = {
        "ok": True,
        "implemented": True,
        "command": "raw add",
        "dry_run": dry_run,
        "raw_id": raw_id,
        "kind": selected_kind,
        "format": selected_format,
        "path": str(target_path),
        "relative_path": _relative_to_vault(config, target_path),
        "metadata_path": str(metadata_path),
        "relative_metadata_path": _relative_to_vault(config, metadata_path),
        "metadata": metadata,
        "would_write": [str(target_path), str(metadata_path)],
        "next_steps": [
            "Inspect the staged file with `memora raw inspect`.",
            "Have the agent read and analyze the raw material before saving curated evidence with `memora source add`.",
        ],
    }
    if dry_run:
        return payload
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    metadata_path.write_text(json_module.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _source_add_payload(
    config: Any,
    path: Path,
    *,
    extract: Optional[Path],
    kind: str,
    source_format: str,
    title: Optional[str],
    url: Optional[str],
    project: Optional[str],
    sensitivity: str,
    tags: list[str],
) -> dict[str, Any]:
    source_path = path.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"source file not found: {source_path}")
    extract_path = extract.expanduser().resolve() if extract is not None else None
    if extract_path is not None and not extract_path.is_file():
        raise ValueError(f"extract file not found: {extract_path}")
    selected_kind = _normalize_raw_kind(kind)
    selected_format = _normalize_raw_format(source_format)
    result = save_source_material(
        config,
        title=title or source_path.stem,
        url=url,
        content=_read_text_file(source_path),
        extract=_read_text_file(extract_path) if extract_path is not None else None,
        project=project,
        tags=tags,
        channel=_source_channel_for_kind(selected_kind),
        source_quality="user_provided",
        sensitivity=sensitivity,
        origin={
            "provider": "source_add",
            "kind": selected_kind,
            "format": selected_format,
            "file_name": source_path.name,
            "path": str(source_path),
            "content_hash": _file_content_hash(source_path),
            **(
                {
                    "extract_file_name": extract_path.name,
                    "extract_path": str(extract_path),
                    "extract_content_hash": _file_content_hash(extract_path),
                }
                if extract_path is not None
                else {}
            ),
        },
    )
    payload = result.to_dict()
    payload.update(
        {
            "command": "source add",
            "kind": selected_kind,
            "format": selected_format,
            "next_steps": [
                "Review the saved source/extract under Sources/.",
                "Promote only durable atomic facts, decisions, preferences, tasks, or project context with `memora remember`.",
            ],
        }
    )
    return payload


def _raw_process_one(
    config: Any,
    path: Path,
    *,
    title: Optional[str],
    extract_file: Optional[Path],
    project: Optional[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
    tags: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"raw file not found: {path}")
    if not _is_processable_raw(path):
        raise ValueError(f"raw file is not processable yet: {path.suffix}")
    raw_payload = _raw_file_payload(config, path, include_preview=False)
    extract = _read_text_file(extract_file) if extract_file is not None else None
    if dry_run:
        return {
            "ok": True,
            "implemented": True,
            "dry_run": True,
            **raw_payload,
            "title": title or path.stem,
            "project": project,
            "channel": channel,
            "source_quality": source_quality,
            "sensitivity": sensitivity,
            "tags": tags,
            "would_write": "Sources/<source_id>/source.md",
            "next_steps": [
                "Run without --dry-run to normalize this raw item into Sources/.",
                "Have the agent create an extract and source-backed pending memories for review.",
            ],
        }
    result = save_source_material(
        config,
        title=title or path.stem,
        content=_read_text_file(path),
        extract=extract,
        project=project,
        tags=tags,
        channel=channel,
        source_quality=source_quality,
        sensitivity=sensitivity,
        origin={
            "provider": "raw",
            "file_name": path.name,
            "raw_path": _relative_to_vault(config, path),
            "content_hash": raw_payload["content_hash"],
        },
        slug=path.stem,
    ).to_dict()
    result.update(
        {
            "dry_run": False,
            "raw_path": raw_payload["relative_path"],
            "content_hash": raw_payload["content_hash"],
            "idempotency_key": raw_payload["idempotency_key"],
            "next_steps": [
                "Create or review the source extract before promoting canonical memory.",
                "Use memora review --group-by source after pending memories are created.",
            ],
        }
    )
    return result


def _maybe_refresh_index(config: Any, *, before: str, refresh: Optional[bool]) -> dict[str, Any]:
    freshness_config = config.index_freshness
    if before == "search":
        configured = freshness_config.refresh_before_search
    elif before == "recall":
        configured = freshness_config.refresh_before_recall
    else:
        raise ValueError("before must be 'search' or 'recall'")
    enabled = configured if refresh is None else refresh
    if not enabled:
        return {
            "enabled": freshness_config.enabled,
            "trigger": f"before_{before}",
            "skipped": True,
            "reason": "disabled_for_operation",
        }
    payload = refresh_index_if_needed(config).to_dict()
    payload.update({"trigger": f"before_{before}", "skipped": False})
    return payload


def _resolve_task_policy(config: Any, task_class: str) -> tuple[str, Any]:
    selected = (task_class or "default").strip().lower()
    policy = config.recall_policies.get(selected)
    if policy is None:
        selected = "default"
        policy = config.recall_policies.get(selected)
    return selected, policy


def _cli_budget(budget: Optional[int], task_policy: Any) -> int:
    selected = task_policy.budget if budget is None else int(budget)
    if selected < 1:
        raise ValueError("budget must be at least 1")
    return selected


def _resolve_profile_request(config: Any, task_policy: Any, override: Optional[bool]) -> tuple[bool, list[str]]:
    sources: list[str] = []
    if override is not None:
        return bool(override), ["cli"]
    requested = False
    if getattr(config.profile, "inject_by_default", False):
        requested = True
        sources.append("config")
    if getattr(task_policy, "include_profile", False):
        requested = True
        sources.append("task_policy")
    return requested, sources


def _policy_skipped_profile_payload(
    config: Any,
    *,
    requested: bool,
    request_sources: list[str],
    project: Optional[str],
    task_budget: int,
) -> dict[str, Any]:
    payload = build_context_profile_payload(
        config,
        requested=False,
        request_sources=request_sources,
        project=project,
        task_budget=task_budget,
    )
    payload["requested"] = requested
    payload["reason"] = "policy_skipped" if requested else "profile_injection_disabled"
    return payload


def _compose_context_markdown(profile: Mapping[str, Any], brief_markdown: str) -> str:
    profile_markdown = str(profile.get("markdown") or "") if profile.get("included") else ""
    if not profile_markdown:
        return brief_markdown
    if not brief_markdown:
        return profile_markdown
    return f"{profile_markdown.rstrip()}\n\n{brief_markdown}"


def _build_context_trace(
    policy: Mapping[str, Any],
    *,
    task_class: str,
    task_policy: Any,
    profile: Mapping[str, Any],
    task_budget: int,
    retrieval: Optional[Any] = None,
    freshness: Optional[Any] = None,
    session: Optional[Any] = None,
    selected_count: int = 0,
    empty_reason: Optional[str] = None,
) -> dict[str, Any]:
    retrieval_trace = dict(retrieval) if isinstance(retrieval, Mapping) else {}
    freshness_trace = dict(freshness) if isinstance(freshness, Mapping) else None
    policy_query = str(policy.get("query") or "")
    planned = retrieval_trace.get("planned_query_variants")
    if not isinstance(planned, list):
        planned = [policy_query] if policy_query else []
    semantic = retrieval_trace.get("semantic")
    if not isinstance(semantic, Mapping):
        semantic = {
            "status": "not_used",
            "enabled": False,
            "provider": None,
            "model": None,
        }
    attempts = retrieval_trace.get("attempted_searches")
    if not isinstance(attempts, list):
        attempts = []
    session_trace_payload = dict(session) if isinstance(session, Mapping) else None

    profile_budget = int(profile.get("budget") or 0)
    payload: dict[str, Any] = {
        "policy": dict(policy),
        "task_class": task_class,
        "recall_policy": task_policy.model_dump(mode="json"),
        "task_budget": {
            "selected": task_budget,
            "brief": task_budget,
            "profile": profile_budget,
            "profile_used": int(profile.get("used_tokens_estimate") or 0),
        },
        "profile": dict(profile),
        "policy_query": policy_query,
        "planned_query_variants": planned,
        "mode": retrieval_trace.get("mode"),
        "requested_mode": retrieval_trace.get("requested_mode"),
        "semantic": dict(semantic),
        "attempted_searches": attempts,
        "freshness": freshness_trace,
        "selected_count": selected_count,
        "empty_reason": empty_reason or retrieval_trace.get("empty_reason"),
        "recall_ladder": [
            {
                "step": "policy",
                "memory_needed": bool(policy.get("should_recall")),
            },
            {
                "step": "profile",
                "included": bool(profile.get("included")),
                "reason": profile.get("reason"),
            },
            {
                "step": "brief",
                "selected_count": selected_count,
                "empty_reason": empty_reason or retrieval_trace.get("empty_reason"),
            },
        ],
    }
    if session_trace_payload:
        payload["session"] = session_trace_payload
    return payload


def _agent_capture_payload(
    config: Any,
    *,
    source_title: Optional[str],
    source_file: Path,
    summary_file: Path,
    memories_file: Path,
    project: Optional[str],
    tags: list[str],
    sensitivity: str,
    confidence: float,
    dry_run: bool,
) -> dict[str, Any]:
    source_path = source_file.expanduser()
    summary_path = summary_file.expanduser()
    source_content = source_path.read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8")
    proposals = _load_memory_proposals(memories_file)
    selected_sensitivity = _agent_sensitivity(sensitivity)
    selected_tags = _unique_strings(tags)
    title = (source_title or source_path.stem).strip() or source_path.stem
    origin = {
        "provider": "agent_capture",
        "file_name": source_path.name,
        "path": str(source_path),
        "summary_file_name": summary_path.name,
        "summary_path": str(summary_path),
    }

    if dry_run:
        source_payload = _planned_agent_source_payload(
            title=title,
            content=source_content,
            extract=summary,
            project=project,
            tags=selected_tags,
            channel="file",
            source_quality="agent_fetched",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_ref = _source_ref_payload(source_payload)
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=selected_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="agent capture",
            dry_run=True,
        )
    else:
        saved_source = save_source_material(
            config,
            title=title,
            content=source_content,
            extract=summary,
            project=project,
            tags=selected_tags,
            channel="file",
            source_quality="agent_fetched",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_payload = saved_source.to_dict()
        source_ref = _source_ref_payload(source_payload)
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=selected_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="agent capture",
            dry_run=False,
        )

    memories = proposal_result["memories"]
    pending_count = sum(1 for memory in memories if memory.get("status") == LifecycleStatus.PENDING.value)
    return {
        "ok": True,
        "implemented": True,
        "command": "agent capture",
        "dry_run": dry_run,
        "would_write": True,
        "written": not dry_run,
        "review_required": pending_count > 0,
        "source": source_payload,
        "memories": memories,
        "planned_memories": memories if dry_run else [],
        "created_memories": [] if dry_run else memories,
        "rejected_proposals": proposal_result["rejected_proposals"],
        "errors": proposal_result["rejected_proposals"],
        "memory_count": len(memories),
        "pending_count": pending_count,
        "rejected_count": len(proposal_result["rejected_proposals"]),
    }


def _scheduled_ingest_payload(
    config: Any,
    *,
    kind: str,
    project: Optional[str],
    source_file: Path,
    extract_file: Path,
    memories_file: Path,
    tags: list[str],
    sensitivity: str,
    confidence: float,
    dry_run: bool,
) -> dict[str, Any]:
    source_path = source_file.expanduser()
    extract_path = extract_file.expanduser()
    source_content = source_path.read_text(encoding="utf-8")
    extract = extract_path.read_text(encoding="utf-8")
    proposals = _load_memory_proposals(memories_file)
    selected_kind = normalize_scheduled_kind(kind)
    channel = scheduled_source_channel(selected_kind)
    selected_sensitivity = _agent_sensitivity(sensitivity)
    selected_tags = _unique_strings(tags)
    origin = {
        "provider": "scheduled_ingest",
        "kind": selected_kind,
        "file_name": source_path.name,
        "path": str(source_path),
        "extract_file_name": extract_path.name,
        "extract_path": str(extract_path),
    }

    if dry_run:
        source_payload = _planned_agent_source_payload(
            title=source_path.stem,
            content=source_content,
            extract=extract,
            project=project,
            tags=selected_tags,
            channel=channel,
            source_quality="imported_export",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_ref = _source_ref_payload(source_payload)
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=selected_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="scheduled ingest",
            dry_run=True,
        )
    else:
        saved_source = save_source_material(
            config,
            title=source_path.stem,
            content=source_content,
            extract=extract,
            project=project,
            tags=selected_tags,
            channel=channel,
            source_quality="imported_export",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_payload = saved_source.to_dict()
        source_ref = _source_ref_payload(source_payload)
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=selected_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="scheduled ingest",
            dry_run=False,
        )

    memories = proposal_result["memories"]
    pending_count = sum(1 for memory in memories if memory.get("status") == LifecycleStatus.PENDING.value)
    return {
        "ok": True,
        "implemented": True,
        "command": "scheduled ingest",
        "kind": selected_kind,
        "channel": channel,
        "dry_run": dry_run,
        "would_write": True,
        "written": not dry_run,
        "review_required": pending_count > 0,
        "source": source_payload,
        "memories": memories,
        "planned_memories": memories if dry_run else [],
        "created_memories": [] if dry_run else memories,
        "rejected_proposals": proposal_result["rejected_proposals"],
        "errors": proposal_result["rejected_proposals"],
        "memory_count": len(memories),
        "pending_count": pending_count,
        "rejected_count": len(proposal_result["rejected_proposals"]),
    }


def _session_finalize_payload(
    config: Any,
    *,
    transcript: Path,
    summary_file: Path,
    memories_file: Optional[Path],
    session_format: str,
    project: Optional[str],
    tags: list[str],
    sensitivity: str,
    confidence: float,
    dry_run: bool,
) -> dict[str, Any]:
    transcript_path = transcript.expanduser()
    summary_path = summary_file.expanduser()
    transcript_content = transcript_path.read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8")
    proposals = _load_memory_proposals(memories_file) if memories_file is not None else []
    selected_sensitivity = _agent_sensitivity(sensitivity)
    session_tags = _unique_strings([*tags, "ai-session"])
    origin = {
        "provider": "file",
        "file_name": transcript_path.name,
        "path": str(transcript_path),
        "format": session_format,
        "summary_file_name": summary_path.name,
        "summary_path": str(summary_path),
    }

    if dry_run:
        source_payload = _planned_agent_source_payload(
            title=transcript_path.stem,
            content=transcript_content,
            extract=summary,
            project=project,
            tags=session_tags,
            channel="ai_session",
            source_quality="imported_export",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_ref = _source_ref_payload(source_payload)
        summary_memory = _planned_memory_payload(
            proposal_index=None,
            memory_type=MemoryType.CONVERSATION_SUMMARY,
            text=summary,
            scope=MemoryScope.PROJECT if project else None,
            project=project,
            tags=session_tags,
            confidence=confidence,
            source_ref=source_ref,
            author_name="session finalize",
            risk_flags=source_payload["risk_flags"],
        )
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=session_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="session finalize",
            dry_run=True,
        )
    else:
        saved_source = save_source_material(
            config,
            title=transcript_path.stem,
            content=transcript_content,
            extract=summary,
            project=project,
            tags=session_tags,
            channel="ai_session",
            source_quality="imported_export",
            sensitivity=selected_sensitivity,
            origin=origin,
        )
        source_payload = saved_source.to_dict()
        source_ref = _source_ref_payload(source_payload)
        summary_result = remember_memory(
            config,
            memory_type=MemoryType.CONVERSATION_SUMMARY,
            text=summary,
            scope=MemoryScope.PROJECT if project else None,
            project=project,
            status=LifecycleStatus.PENDING,
            tags=session_tags,
            author_kind=AuthorKind.AGENT,
            author_name="session finalize",
            source=source_ref,
            confidence=confidence,
            risk_flags=source_payload["risk_flags"],
        )
        summary_memory = _created_memora_payload(
            summary_result,
            proposal_index=None,
            source_ref=source_ref,
            author_name="session finalize",
            confidence=confidence,
        )
        proposal_result = _process_memora_proposals(
            config,
            proposals,
            source_ref=source_ref,
            default_project=project,
            default_tags=session_tags,
            default_confidence=confidence,
            risk_flags=source_payload["risk_flags"],
            author_name="session finalize",
            dry_run=False,
        )

    atomic_memories = proposal_result["memories"]
    memories = [summary_memory, *atomic_memories]
    pending_count = sum(1 for memory in memories if memory.get("status") == LifecycleStatus.PENDING.value)
    return {
        "ok": True,
        "implemented": True,
        "command": "session finalize",
        "dry_run": dry_run,
        "would_write": True,
        "written": not dry_run,
        "review_required": pending_count > 0,
        "format": session_format,
        "source": source_payload,
        "summary_memory": summary_memory,
        "memories": memories,
        "atomic_memories": atomic_memories,
        "planned_memories": memories if dry_run else [],
        "created_memories": [] if dry_run else memories,
        "rejected_proposals": proposal_result["rejected_proposals"],
        "errors": proposal_result["rejected_proposals"],
        "memory_count": len(memories),
        "atomic_memory_count": len(atomic_memories),
        "pending_count": pending_count,
        "rejected_count": len(proposal_result["rejected_proposals"]),
    }


def _process_memora_proposals(
    config: Any,
    proposals: list[Any],
    *,
    source_ref: dict[str, Any],
    default_project: Optional[str],
    default_tags: list[str],
    default_confidence: float,
    risk_flags: Iterable[str],
    author_name: str,
    dry_run: bool,
) -> dict[str, Any]:
    memories: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals):
        try:
            planned = _normalize_memora_proposal(
                proposal,
                index=index,
                source_ref=source_ref,
                default_project=default_project,
                default_tags=default_tags,
                default_confidence=default_confidence,
            )
        except Exception as exc:
            rejected.append(_proposal_error(index, proposal, "invalid_proposal", str(exc)))
            continue

        if dry_run:
            memories.append(
                _planned_memory_payload(
                    proposal_index=index,
                    memory_type=planned["memory_type"],
                    text=planned["text"],
                    scope=planned["scope"],
                    project=planned["project"],
                    tags=planned["tags"],
                    confidence=planned["confidence"],
                    source_ref=planned["source_ref"],
                    author_name=author_name,
                    risk_flags=risk_flags,
                )
            )
            continue

        result = remember_memory(
            config,
            memory_type=planned["memory_type"],
            text=planned["text"],
            scope=planned["scope"],
            project=planned["project"],
            status=LifecycleStatus.PENDING,
            tags=planned["tags"],
            author_kind=AuthorKind.AGENT,
            author_name=author_name,
            source=planned["source_ref"],
            confidence=planned["confidence"],
            risk_flags=risk_flags,
        )
        memories.append(
            _created_memora_payload(
                result,
                proposal_index=index,
                source_ref=planned["source_ref"],
                author_name=author_name,
                confidence=planned["confidence"],
            )
        )
    return {"memories": memories, "rejected_proposals": rejected}


def _normalize_memora_proposal(
    proposal: Any,
    *,
    index: int,
    source_ref: dict[str, Any],
    default_project: Optional[str],
    default_tags: list[str],
    default_confidence: float,
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise ValueError("proposal must be a JSON object")

    raw_type = _clean_optional_string(proposal.get("type")) or MemoryType.FACT.value
    try:
        memory_type = MemoryType(raw_type)
    except ValueError as exc:
        raise ValueError(f"unsupported memory type: {raw_type}") from exc
    if memory_type not in AGENT_CAPTURE_ALLOWED_MEMORY_TYPES:
        allowed = ", ".join(sorted(memory_type.value for memory_type in AGENT_CAPTURE_ALLOWED_MEMORY_TYPES))
        raise ValueError(f"unsupported memory type for batch capture: {memory_type.value}; allowed: {allowed}")

    text = _memory_text_from_proposal(proposal)
    raw_confidence = proposal.get("confidence", default_confidence)
    try:
        selected_confidence = float(raw_confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be a number between 0 and 1") from exc
    if selected_confidence < 0 or selected_confidence > 1:
        raise ValueError("confidence must be between 0 and 1")

    scope = _proposal_scope(proposal, default_project=default_project)
    project = _clean_optional_string(proposal.get("project")) or default_project
    tags = _proposal_tags(proposal, default_tags)
    proposal_source = proposal.get("source")
    selected_source = dict(source_ref)
    if proposal_source is not None:
        if not isinstance(proposal_source, Mapping):
            raise ValueError("source must be a JSON object when provided")
        selected_source.update(
            {
                str(key): value
                for key, value in proposal_source.items()
                if _clean_optional_string(value) is not None
            }
        )
    if not selected_source.get("path") and not selected_source.get("url"):
        raise ValueError("source must include path or url")

    return {
        "index": index,
        "memory_type": memory_type,
        "text": text,
        "scope": scope,
        "project": project,
        "tags": tags,
        "confidence": selected_confidence,
        "source_ref": selected_source,
    }


def _load_memory_proposals(path: Optional[Path]) -> list[Any]:
    if path is None:
        return []
    payload = json_module.loads(path.expanduser().read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("memories"), list):
        return list(payload["memories"])
    raise ValueError("memories-file must be a JSON list or an object with a memories list")


def _planned_agent_source_payload(
    *,
    title: str,
    content: str,
    extract: str,
    project: Optional[str],
    tags: list[str],
    channel: str,
    source_quality: str,
    sensitivity: str,
    origin: Mapping[str, str],
) -> dict[str, Any]:
    safety = scan_source_material(
        content=content,
        extract=extract,
        metadata={
            "channel": channel,
            "source_quality": source_quality,
            "sensitivity": sensitivity,
            **dict(origin),
        },
    )
    return {
        "ok": True,
        "implemented": True,
        "dry_run": True,
        "source_id": "<source_id>",
        "source_dir": "Sources/<source_id>",
        "relative_dir": "Sources/<source_id>",
        "source_path": "Sources/<source_id>/source.md",
        "relative_source_path": "Sources/<source_id>/source.md",
        "extract_path": "Sources/<source_id>/extract.md",
        "relative_extract_path": "Sources/<source_id>/extract.md",
        "url": None,
        "title": title,
        "project": project,
        "tags": tags,
        "channel": channel,
        "source_quality": source_quality,
        "sensitivity": sensitivity,
        "origin": dict(origin),
        "risk_flags": list(safety.risk_flags),
        "safety": safety.to_dict(),
        "citations": [
            {"id": "<source_id>", "path": "Sources/<source_id>/source.md", "kind": "source"},
            {"id": "<source_id>", "path": "Sources/<source_id>/extract.md", "kind": "source_extract"},
        ],
        "would_write": "Sources/<source_id>/{source.md,extract.md}",
    }


def _planned_memory_payload(
    *,
    proposal_index: Optional[int],
    memory_type: MemoryType,
    text: str,
    scope: Optional[MemoryScope],
    project: Optional[str],
    tags: list[str],
    confidence: float,
    source_ref: dict[str, Any],
    author_name: str,
    risk_flags: Iterable[str],
) -> dict[str, Any]:
    return {
        "ok": True,
        "planned": True,
        "proposal_index": proposal_index,
        "id": "<memory_id>",
        "path": f"Memories/<{memory_type.value}>/<memory_id>.md",
        "relative_path": f"Memories/<{memory_type.value}>/<memory_id>.md",
        "type": memory_type.value,
        "text": text,
        "scope": scope.value if scope else None,
        "project": project,
        "status": LifecycleStatus.PENDING.value,
        "confidence": confidence,
        "tags": tags,
        "risk_flags": list(risk_flags),
        "source": dict(source_ref),
        "author": {"kind": AuthorKind.AGENT.value, "name": author_name},
        "review_required": True,
        "would_write": f"Memories/<{memory_type.value}>/<memory_id>.md",
    }


def _created_memora_payload(
    result: Any,
    *,
    proposal_index: Optional[int],
    source_ref: dict[str, Any],
    author_name: str,
    confidence: float,
) -> dict[str, Any]:
    payload = result.to_dict()
    payload.update(
        {
            "proposal_index": proposal_index,
            "source": dict(source_ref),
            "author": {"kind": AuthorKind.AGENT.value, "name": author_name},
            "confidence": confidence,
            "review_required": payload["status"] == LifecycleStatus.PENDING.value,
        }
    )
    return payload


def _source_ref_payload(source_payload: Mapping[str, Any]) -> dict[str, Any]:
    path = source_payload.get("relative_extract_path") or source_payload.get("relative_source_path")
    return {
        "path": path,
        "title": source_payload.get("title"),
        "source_id": source_payload.get("source_id"),
    }


def _resolve_session_transcript(
    transcript_arg: Optional[Path],
    transcript_option: Optional[Path],
) -> Path:
    if transcript_arg is None and transcript_option is None:
        raise ValueError("provide a transcript path as an argument or with --transcript")
    if transcript_arg is not None and transcript_option is not None:
        if transcript_arg.expanduser() != transcript_option.expanduser():
            raise ValueError("provide transcript either as an argument or with --transcript, not both")
    return transcript_option or transcript_arg  # type: ignore[return-value]


def _agent_sensitivity(value: str) -> str:
    selected = value.strip().lower()
    if selected not in AGENT_SOURCE_SENSITIVITIES:
        raise ValueError("sensitivity must be one of: normal, private, secret, unsafe")
    return selected


def _proposal_scope(proposal: Mapping[str, Any], *, default_project: Optional[str]) -> Optional[MemoryScope]:
    raw_scope = _clean_optional_string(proposal.get("scope"))
    if raw_scope:
        return MemoryScope(raw_scope)
    if default_project:
        return MemoryScope.PROJECT
    return None


def _proposal_tags(proposal: Mapping[str, Any], default_tags: list[str]) -> list[str]:
    return _unique_strings(
        [
            *default_tags,
            *_string_list(proposal.get("tags", ())),
            *_string_list(proposal.get("tag", ())),
        ]
    )


def _memory_text_from_proposal(proposal: Mapping[str, Any]) -> str:
    for key in ("text", "body", "content"):
        value = proposal.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("proposal must include non-empty text")


def _proposal_error(index: int, proposal: Any, code: str, message: str) -> dict[str, Any]:
    proposal_type = proposal.get("type") if isinstance(proposal, Mapping) else None
    return {
        "index": index,
        "type": proposal_type,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _unique_strings(values: Iterable[Any]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean_optional_string(value)
        if item is None or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return [str(item) for item in value]
    return [str(value)]


def _clean_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_text_file(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def _relative_to_vault(config: Any, path: Path) -> str:
    try:
        return path.resolve().relative_to(config.vault_path).as_posix()
    except ValueError:
        return str(path)


def _print_json(payload: dict[str, Any]) -> None:
    typer.echo(json_module.dumps(payload, indent=2, sort_keys=True))


def _print_agent_operation_results(payload: Mapping[str, Any], *, dry_run_label: str) -> None:
    label = dry_run_label if payload.get("dry_run") else "Agent integration"
    console.print(f"[green]{label}:[/green] {payload['would_write_count']} writable, {payload['blocked_count']} blocked")
    for result in payload["results"]:
        if result["blocked"]:
            status = "blocked: manual merge needed"
        elif result["written"]:
            status = "written"
        elif result["would_write"]:
            status = "would write"
        else:
            status = str(result["action"])
        console.print(f"- {result['client']}: {status} -> {result['target_path']}")


def _agent_vault_status_probe(vault: Optional[Path]) -> dict[str, Any]:
    try:
        config = load_config(vault)
        return {
            "ok": True,
            "explicit": vault is not None,
            "vault_path": str(config.vault_path),
            "status": status_summary(config),
        }
    except Exception as exc:
        return {
            "ok": False,
            "explicit": vault is not None,
            "message": str(exc),
        }


def _handle_error(exc: Exception, *, json_output: bool, code: str) -> None:
    message = str(exc)
    if isinstance(exc, ConfigError):
        code = "config_error"

    if json_output:
        _print_json({"ok": False, "error": {"code": code, "message": message}})
    else:
        console.print(f"[red]{code}:[/red] {message}")
    raise typer.Exit(1)


def main() -> None:
    """Run the Typer CLI when invoked as `python -m cli`."""

    app()


__all__ = ["app", "main"]


if __name__ == "__main__":  # pragma: no cover
    main()
