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

Implemented in Stage 2.

Creates the vault structure and `.agent-memory/config.yaml`. It does not
overwrite user content or an existing config.

Created folders:

- `Memories/facts`
- `Memories/preferences`
- `Memories/decisions`
- `Memories/tasks`
- `Memories/sources`
- `Memories/projects`
- `Memories/conversations`
- `Sources`
- `Briefs`
- `Profiles/projects`
- `Synthesis`
- `.agent-memory/schemas`
- `.agent-memory/cache`
- `.agent-memory/embeddings`
- `.agent-memory/locks`

Example:

```bash
memory init ./memory-vault --json
```

### `memory remember`

Implemented in Stage 2.

Creates a valid Stage 1 Markdown memory file under the matching
`Memories/<type>` subfolder. The initial CLI writes user-authored memories with
`active` status by default, includes one observation that mirrors the body text,
and validates the rendered Markdown before saving it.

The command loads config from `--vault`, `AGENT_MEMORY_VAULT`, or the nearest
parent `.agent-memory/config.yaml`.

Example:

```bash
memory remember --vault ./memory-vault --type decision --text "Use Markdown as durable memory." --json
```

### `memory reindex`

Implemented in Stage 4.

Rebuilds `.agent-memory/index.sqlite` from canonical Markdown under
`Memories/**/*.md`. The index is disposable cache data and can be recreated at
any time from the vault files.

Behavior:

- Parses YAML frontmatter with the shared schema validator.
- Computes document and chunk `content_hash` values.
- Skips unchanged documents and preserves their existing chunks.
- Populates `documents`, `memories`, `chunks`, `observations`, `links`, and
  `chunk_fts`.
- Reports graph orphan warnings for relation targets that are not present in the
  vault.

Use `--clean` to delete the existing SQLite file before rebuilding.

Example:

```bash
memory reindex --vault ./memory-vault --json
memory reindex --vault ./memory-vault --clean
```

### `memory search`

Stage 2 placeholder.

The command accepts `query` and supports `--json`. Ranked keyword, graph, and
semantic retrieval are planned for later stages.

### `memory recall`

Stage 2 placeholder.

The command accepts `query` and `--budget`. Budgeted context packing with
citations is planned for later stages.

### `memory brief`

Stage 2 placeholder.

The command accepts `query` and `--budget`. Brief generation is planned for a
later stage.

### `memory status`

Implemented in Stage 2.

Loads config, validates canonical memory Markdown with the Stage 1 validator,
and returns a lightweight summary including memory count, pending count, issue
count, and whether the disposable SQLite index exists.

### `memory doctor`

Expanded in Stage 4.

Runs schema validation across `Memories/**/*.md` and validates durable graph
references. Missing relation targets are reported as graph issues and cause the
command to exit non-zero. Lifecycle consistency, missing source checks, and
index rebuildability checks are planned for later stages.

### `memory import`

Stage 2 placeholder.

The command accepts a source path and supports `--json`. Markdown and Basic
Memory-compatible import are planned for later stages.

### `memory export`

Stage 2 placeholder.

The command accepts `--format markdown` and supports `--json`. Export is planned
for a later stage.

## JSON Output

All CLI commands support `--json` so coding agents can consume stable,
structured responses. Retrieval and lifecycle commands that are not yet
implemented return `implemented: false` while preserving the intended command
signatures.

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
