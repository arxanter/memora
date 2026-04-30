"""Typer-based command line interface for Agent Memory."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from agent_memory.config import ConfigError, load_config
from agent_memory.indexer import reindex_vault
from agent_memory.recall import recall_memory
from agent_memory.retrieval import RetrievalIndexError, SearchFilters, search_memory
from agent_memory.schema import LifecycleStatus, MemoryScope, MemoryType
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


@app.command()
def brief(
    query: str = typer.Argument(..., help="Brief query."),
    budget: int = typer.Option(1200, "--budget", min=1, help="Token budget."),
    vault: Optional[Path] = typer.Option(None, "--vault", "-v", help="Vault path."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Placeholder for agent-oriented memory brief generation."""

    _placeholder_command("brief", vault=vault, json_output=json_output, query=query, budget=budget, brief=None)


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
    else:
        console.print(f"[red]Doctor found {len(payload['issues'])} issue(s).[/red]")
        for issue in payload["issues"]:
            console.print(f"- {issue['path']}: {issue['message']}")
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


def _print_json(payload: dict[str, Any]) -> None:
    typer.echo(json_module.dumps(payload, indent=2, sort_keys=True))


def _handle_error(exc: Exception, *, json_output: bool, code: str) -> None:
    message = str(exc)
    if isinstance(exc, ConfigError):
        code = "config_error"

    if json_output:
        _print_json({"ok": False, "error": {"code": code, "message": message}})
    else:
        console.print(f"[red]{code}:[/red] {message}")
    raise typer.Exit(1)


__all__ = ["app"]
