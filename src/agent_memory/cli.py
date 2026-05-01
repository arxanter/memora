"""Typer-based command line interface for Agent Memory."""

from __future__ import annotations

import json as json_module
import os
import shutil
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from agent_memory.brief import brief_memory
from agent_memory.config import ConfigError, load_config
from agent_memory.evaluation import run_evaluation
from agent_memory.freshness import refresh_index_if_needed
from agent_memory.indexer import reindex_vault
from agent_memory.lifecycle import (
    contradict_memories,
    curation_plan,
    decay_memories,
    mark_status,
    reject_memory,
    review_queue,
    supersede_memory,
)
from agent_memory.recall import explain_recall, recall_memory
from agent_memory.recall_policy import should_recall
from agent_memory.retrieval import RetrievalIndexError, SearchFilters, search_memory
from agent_memory.schema import AuthorKind, LifecycleStatus, MemoryScope, MemoryType
from agent_memory.sources import save_source_material
from agent_memory.synthesis import write_synthesis
from agent_memory.sync import detect_sync_conflicts
from agent_memory.ux import graph_memory, inspect_memory, open_memory
from agent_memory.vault import (
    doctor_report,
    init_vault,
    placeholder_result,
    remember_memory,
    status_summary,
)

app = typer.Typer(
    help="Local-first Obsidian-backed memory CLI.",
    no_args_is_help=True,
)
console = Console()
MCP_CONFIG_FORMATS = {"generic", "claude", "cursor"}
SOURCE_INBOX_SUFFIXES = {".md", ".markdown", ".txt"}
HELP_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Setup and health",
        (
            ("init <vault>", "Create the vault layout and config."),
            ("status", "Show vault health and local index state."),
            ("doctor", "Validate Markdown schema, graph links, and conflicts."),
            ("conflicts", "Detect sync conflict markers, duplicate IDs, and invalid frontmatter."),
            ("reindex", "Rebuild the disposable SQLite index from Markdown."),
            ("refresh-index", "Reindex only when durable vault files changed."),
            ("mcp-config", "Print MCP client configuration for Claude, Cursor, or generic clients."),
        ),
    ),
    (
        "Write and lifecycle",
        (
            ("remember", "Create a validated Markdown memory."),
            ("review", "List pending agent-generated memories with a diff-style preview."),
            ("curate", "Propose conservative review actions without mutating memories."),
            ("mark", "Set a memory lifecycle status."),
            ("reject", "Reject a memory so default retrieval excludes it."),
            ("supersede", "Mark an old memory replaced by a newer one."),
            ("contradict", "Record a contradiction relation between memories."),
            ("decay", "Mark expired active memories stale."),
            ("import-source <path>", "Save a Markdown/text file as source material under Sources/."),
            ("import-source-inbox <path>", "Import Markdown/text files from a source inbox directory."),
            ("import-session <path>", "Save an AI-agent transcript and optional summary memory."),
        ),
    ),
    (
        "Retrieval and agent context",
        (
            ("search", "Return ranked memory results with snippets and citations."),
            ("recall", "Pack ranked chunks under a strict token budget."),
            ("explain-recall", "Explain selected and skipped recall candidates."),
            ("brief", "Render a citation-preserving Memory Brief."),
            ("synthesize", "Write a deterministic generated synthesis under Synthesis/."),
            ("should-recall", "Decide whether a user request should use memory."),
        ),
    ),
    (
        "Inspect, evaluation, and compatibility",
        (
            ("inspect <id>", "Show one memory by ID."),
            ("open <id>", "Print a memory Markdown path and Obsidian URI."),
            ("graph <id>", "Show incoming and outgoing graph links."),
            ("eval <fixture-or-file>", "Run deterministic fixture-backed evaluation cases."),
            ("import <path>", "Placeholder for Markdown/Basic Memory import."),
            ("export", "Placeholder for Markdown export."),
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


@app.command("help")
def help_command(
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show Agent Memory commands grouped by workflow."""

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
            "Run `memory <command> --help` for command-specific options.",
            "Most commands support `--json` for agent-friendly output.",
            "Use `memory mcp-config` to print Claude/Cursor MCP configuration.",
        ],
    }
    if json_output:
        _print_json(payload)
        return

    console.print("[bold]Agent Memory commands[/bold]")
    console.print("Run [cyan]memory <command> --help[/cyan] for command-specific options.")
    console.print("Most commands support [cyan]--json[/cyan].\n")
    for group in payload["groups"]:
        console.print(f"[bold]{group['name']}[/bold]")
        for command in group["commands"]:
            console.print(f"  [cyan]{command['usage']:<24}[/cyan] {command['description']}")
        console.print("")
    console.print("MCP setup: [cyan]memory mcp-config --format claude[/cyan]")


@app.command("mcp-config")
def mcp_config(
    config_format: str = typer.Option("generic", "--format", help="Config format: generic, claude, or cursor."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    command: Optional[Path] = typer.Option(None, "--command", help="Path to memory-mcp wrapper."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON with metadata."),
) -> None:
    """Print MCP client configuration for Agent Memory."""

    try:
        selected_format = config_format.strip().lower()
        if selected_format not in MCP_CONFIG_FORMATS:
            raise ValueError("format must be one of: generic, claude, cursor")
        payload = _mcp_config_payload(vault=vault, command=command, config_format=selected_format)
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="mcp_config_failed")

    if json_output:
        _print_json(payload)
        return

    typer.echo(json_module.dumps(payload["config"], indent=2))


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


@app.command("import-source")
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


@app.command("import-source-inbox")
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


@app.command("import-session")
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


@app.command("refresh-index")
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
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Search indexed memory using keyword, optional semantic, metadata, and graph signals."""

    try:
        config = load_config(vault)
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
    budget: int = typer.Option(1200, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this query.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Recall ranked memory chunks packed under a strict token budget."""

    try:
        config = load_config(vault)
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
            budget=budget,
            include_related=include_related,
            semantic=semantic,
            mode=mode,
        ).to_dict()
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


@app.command("explain-recall")
def explain_recall_command(
    query: str = typer.Argument(..., help="Recall query to explain."),
    budget: int = typer.Option(1200, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this explanation.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Explain why recall selected or skipped candidate chunks."""

    try:
        config = load_config(vault)
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
            budget=budget,
            include_related=include_related,
            semantic=semantic,
            mode=mode,
        ).to_dict()
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


@app.command()
def brief(
    query: str = typer.Argument(..., help="Brief query."),
    budget: int = typer.Option(1200, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    memory_type: Optional[MemoryType] = typer.Option(None, "--type", help="Memory type filter."),
    status: Optional[LifecycleStatus] = typer.Option(None, "--status", help="Lifecycle status filter."),
    scope: Optional[MemoryScope] = typer.Option(None, "--scope", help="Recall scope filter."),
    include_related: bool = typer.Option(False, "--include-related", help="Include graph-related memories."),
    semantic: Optional[bool] = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Override semantic search config for this brief.",
    ),
    mode: str = typer.Option("auto", "--mode", help="Search mode: auto, text, vector, or hybrid."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Generate a citation-preserving memory brief under a strict budget."""

    try:
        config = load_config(vault)
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
            budget=budget,
            include_related=include_related,
            semantic=semantic,
            mode=mode,
        ).to_dict()
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


@app.command("synthesize")
def synthesize_command(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    project: Optional[str] = typer.Option(None, "--project", help="Project filter."),
    title: Optional[str] = typer.Option(None, "--title", help="Synthesis title."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum active memories to include."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Write a deterministic generated synthesis Markdown file."""

    try:
        config = load_config(vault)
        payload = write_synthesis(
            config,
            project=project,
            title=title,
            limit=limit,
        ).to_dict()
    except Exception as exc:
        _handle_error(exc, json_output=json_output, code="synthesize_failed")

    if json_output:
        _print_json(payload)
        return

    console.print(f"[green]Wrote synthesis:[/green] {payload['relative_path']}")
    console.print(f"Memories: {payload['memory_count']}")


@app.command("should-recall")
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
def review(
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    group_by: Optional[str] = typer.Option(None, "--group-by", help="Group human output by: source."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List pending agent-generated memories awaiting review."""

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


@app.command()
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


@app.command("eval")
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


@app.command("import")
def import_command(
    path: Path = typer.Argument(..., help="Path to import later."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Placeholder for Markdown and Basic Memory-compatible import."""

    _placeholder_command("import", vault=vault, json_output=json_output, path=str(path))


@app.command("export")
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


def _print_json(payload: dict[str, Any]) -> None:
    typer.echo(json_module.dumps(payload, indent=2, sort_keys=True))


def _mcp_config_payload(
    *,
    vault: Optional[Path],
    command: Optional[Path],
    config_format: str,
) -> dict[str, Any]:
    config = load_config(vault)
    command_path = _resolve_mcp_command(command)
    client_config = {
        "mcpServers": {
            "agent-memory": {
                "command": command_path,
                "env": {
                    "AGENT_MEMORY_VAULT": str(config.vault_path),
                },
            }
        }
    }
    return {
        "ok": True,
        "implemented": True,
        "format": config_format,
        "vault_path": str(config.vault_path),
        "command": command_path,
        "config": client_config,
    }


def _resolve_mcp_command(command: Optional[Path]) -> str:
    if command is not None:
        return str(command.expanduser().resolve())

    found = shutil.which("memory-mcp")
    if found:
        return found

    bin_dir = os.environ.get("AGENT_MEMORY_BIN_DIR")
    if bin_dir:
        candidate = Path(bin_dir).expanduser() / "memory-mcp"
        if candidate.exists():
            return str(candidate.resolve())

    return "memory-mcp"


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
    """Run the Typer CLI when invoked as `python -m agent_memory.cli`."""

    app()


__all__ = ["app", "main"]


if __name__ == "__main__":  # pragma: no cover
    main()
