"""Typer-based command line interface for Memora."""

from __future__ import annotations

import json as json_module
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import typer
from rich.console import Console

from agent_integration import (
    agent_doctor_payload as _agent_doctor_payload,
    agent_group_rules_payload as _agent_group_rules_payload,
    agent_integrate_payload as _agent_integrate_payload,
    agent_scheduled_template_payload as _agent_scheduled_template_payload,
    agent_session_template_payload as _agent_session_template_payload,
    agent_status_payload as _agent_status_payload,
    agent_targets_payload as _agent_targets_payload,
    agent_update_payload as _agent_update_payload,
)
from brief import brief_memory
from config import ConfigError, load_config, set_agent_aliases
from freshness import refresh_index_if_needed
from indexer import reindex_vault
from lifecycle import (
    review_batch_action,
    review_queue,
)
from memora_profile import build_context_profile_payload
from recall import explain_recall, recall_memory
from recall_policy import should_recall
from retrieval import RetrievalIndexError, SearchFilters, search_memory
from safety import scan_source_material
from schema import AuthorKind, LifecycleStatus, MemoryScope, MemoryType
from session import normalize_session_recall_state, session_trace
from sources import lookup_source, save_source_material
from sync import detect_sync_conflicts
from ux import graph_memory, inspect_memory, open_memory
from vault import (
    doctor_report,
    init_vault,
    remember_memory,
    setup_vault,
    status_summary,
)

app = typer.Typer(
    help="Local-first Obsidian-backed Memora CLI.",
    no_args_is_help=True,
)
raw_app = typer.Typer(help="Stage and inspect raw source material.", no_args_is_help=True)
source_app = typer.Typer(help="Save curated durable source evidence.", no_args_is_help=True)
review_app = typer.Typer(help="Review pending agent-generated memories.", no_args_is_help=False)
agent_app = typer.Typer(help="Manage coding-agent integrations.", no_args_is_help=True)
session_app = typer.Typer(help="Finalize AI-agent sessions.", no_args_is_help=True)
app.add_typer(raw_app, name="raw")
app.add_typer(source_app, name="source")
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")
app.add_typer(session_app, name="session")
agent_aliases_app = typer.Typer(
    help="Configure assistant names for recall routing and generated agent rules.",
    no_args_is_help=True,
)
app.add_typer(agent_aliases_app, name="agent-aliases")
console = Console()
RAW_PROCESSABLE_SUFFIXES = {".md", ".markdown", ".txt"}
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
            ("conflicts", "Detect Markdown sync conflicts that require manual resolution."),
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


@app.command()
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
    return path.suffix.lower() in RAW_PROCESSABLE_SUFFIXES


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
