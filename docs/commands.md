# Command Surface

## Principles

The CLI is the development and maintenance interface. MCP is the primary coding-agent interface. Both should call the same underlying services for validation, retrieval, lifecycle handling, and token-budget packing.

Agent-facing operations should support structured JSON responses, stable error codes, and citations. Mutating commands must not silently promote agent-written memory to active durable truth unless explicitly configured.

## Initial CLI Commands

```bash
memory init <vault>
memory remember --type decision --text "..."
memory reindex
memory search "query"
memory recall "query" --budget 1200
memory brief "query" --budget 1200
memory status
memory doctor
memory import <path>
memory export --format markdown
```

### `memory init <vault>`

Creates the vault structure and `.agent-memory/config.yaml`. It should not overwrite user content.

### `memory remember`

Creates a Markdown memory file. Agent-created memories default to `pending`; user-created memories may become `active` immediately.

### `memory reindex`

Rebuilds the local SQLite cache from Markdown. The index is disposable, so this command must be enough to restore local search state after syncing a vault.

### `memory search`

Returns ranked matching memories and chunks using SQLite FTS5 first. Later stages may merge graph and semantic candidates.

### `memory recall`

Returns packed chunks under a strict token budget, with citations for every included chunk.

### `memory brief`

Returns a concise agent-oriented memory brief with stable sections, warnings for stale or superseded context, open questions when known, and citations.

### `memory status`

Summarizes vault health, index freshness, pending memory count, and configured providers.

### `memory doctor`

Validates schema, links, lifecycle consistency, orphaned relations, missing source files, and index rebuildability.

### `memory import`

Imports generic Markdown and Basic Memory-like observations or relations where feasible. Imported material should preserve provenance.

### `memory export`

Exports canonical memories as Markdown and, where practical, Basic Memory-compatible observations and relations.

## MCP Tools

Initial MCP tools:

```text
remember(memory)
search(query, filters)
recall(query, budget, filters)
brief(query, budget, filters)
inspect(id)
explain_recall(query, budget, filters)
mark_status(id, status)
```

MCP responses should include:

- Structured JSON payloads.
- Obsidian-style path citations.
- Lifecycle status for returned memories.
- Enough scoring or selection metadata to support `explain_recall`.

## Mutation Policy

Agent-originated writes default to reviewable `pending` memory. The system should require explicit user review or configuration before pending memory becomes active.

Default retrieval behavior:

- Include `active`.
- Exclude `pending` unless explicitly requested.
- Exclude `rejected`.
- Exclude `superseded` unless explicitly requested or shown as a warning.
- Include `stale` only as warning context when relevant.
