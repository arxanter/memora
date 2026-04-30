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

Implemented in Stage 5, with optional semantic retrieval added in Stage 6.

Searches the Stage 4 SQLite FTS index and, when `semantic.provider` is
configured, lazily generates chunk embeddings for hybrid keyword plus vector
retrieval. It returns ranked memory-level results with snippets, score
breakdowns, metadata, and Obsidian-relative citations. SQLite and embeddings
remain disposable cache data; search does not silently rebuild a missing or
incomplete index. Run `memory reindex --vault <vault>` first if search reports
`index_missing`.

Default retrieval includes `active` and `stale` memory, excludes `pending`,
`rejected`, and `superseded`, and applies stale/superseded penalties. Pass
`--status` to search one explicit lifecycle status.

Supported filters:

- `--project <name>`
- `--type <fact|preference|decision|task|source_extract|project_context|conversation_summary>`
- `--status <pending|active|stale|superseded|rejected>`
- `--scope <user|project|global>`
- `--created-after <date-or-datetime>` and `--created-before <date-or-datetime>`
- `--updated-after <date-or-datetime>` and `--updated-before <date-or-datetime>`
- `--valid-from <date>` and `--valid-to <date>`
- `--include-related` to include linked graph neighbors from the `links` table
- `--semantic` or `--no-semantic` to override the config for one query
- `--limit <n>`

Scoring is deterministic for a fixed index and provider. It combines FTS rank,
optional semantic similarity, graph neighbor boost, memory type boost, status
boost, confidence boost, recency boost, rating boost, stale penalty, and
superseded penalty. Recency is calculated relative to the newest indexed result
in the candidate set, not wall-clock time.

Example:

```bash
memory search "vector db" --vault ./memory-vault --project foo --type decision --status active --json
memory search "agent memory" --vault ./memory-vault --include-related
memory search "database decisions" --vault ./memory-vault --semantic
```

Semantic search is disabled by default. Configure a provider in
`.agent-memory/config.yaml` to enable it for normal searches; see
`docs/semantic-search.md` for local/offline setup.

### `memory recall`

Implemented in Stage 7.

Searches indexed memory with the same retrieval layer as `memory search`, then
packs ranked chunks under a strict estimated token budget. The JSON response
includes `budget`, `used_tokens_estimate`, packed `chunks`, and a citation object
for every packed chunk.

Packing behavior is deterministic for a fixed index and search configuration:

- Default lifecycle handling includes `active` and `stale` memory and excludes
  `pending`, `rejected`, and `superseded`; pass `--status` for an explicit
  lifecycle recall.
- Memories superseded by graph links are excluded by default unless an explicit
  status filter is used.
- Near-identical chunks are deduplicated before packing.
- Chunk selection is reranked by retrieval score plus small chunk-type metadata
  boosts.
- Per-document, per-memory-type, and per-project caps are enforced from
  `.agent-memory/config.yaml` recall settings.
- Oversized chunks are truncated deterministically from the start of the chunk,
  then re-estimated, rather than skipped.
- `used_tokens_estimate` never exceeds `budget` according to the built-in
  deterministic token estimator.

Supported filters:

- `--project <name>`
- `--type <fact|preference|decision|task|source_extract|project_context|conversation_summary>`
- `--status <pending|active|stale|superseded|rejected>`
- `--scope <user|project|global>`
- `--include-related` to include linked graph-related memories before packing
- `--semantic` or `--no-semantic` to override the config for one recall

### `memory brief`

Implemented in Stage 8.

Builds an agent-facing Memory Brief from Stage 7 recall output. The brief is
deterministic, citation-preserving, and rendered under a strict estimated token
budget. Markdown is the default output and `--json` returns the same rendered
Markdown with structured sections, citations, budget metadata, and recall
summary data.

Stable Markdown section shape:

```markdown
## Memory Brief

Current relevant facts:

Current decisions:

Warnings:

Open questions:

Citations:
```

Brief behavior:

- `active` facts, preferences, project context, conversation summaries, and
  source extracts are placed in Current relevant facts.
- `active` decisions are placed in Current decisions.
- `task` memory is placed in Open questions.
- `stale`, `superseded`, `pending`, and `rejected` memory is kept out of the
  main sections and shown only in Warnings when selected.
- `supersedes` graph links add warnings, and `contradicts` graph links add
  conflict bullets under Open questions when connected to selected memory.
- Every bullet carries citation keys such as `[C1]`, and the Citations section
  maps those keys to Obsidian-relative memory paths.

Supported filters match `memory recall`:

- `--project <name>`
- `--type <fact|preference|decision|task|source_extract|project_context|conversation_summary>`
- `--status <pending|active|stale|superseded|rejected>`
- `--scope <user|project|global>`
- `--include-related` to include graph-related memories before brief generation
- `--semantic` or `--no-semantic` to override the config for one brief

Example:

```bash
memory brief "Obsidian sync decisions" --vault ./memory-vault --budget 1200
memory brief "Obsidian sync decisions" --vault ./memory-vault --json
```

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

`search(query, filters)` is implemented in Stage 5 and accepts the same filter
keys as the CLI using snake_case names, plus `include_related`, `semantic`, and
`limit`.
`recall(query, budget, filters)` is implemented in Stage 7 and accepts the same
filter keys, plus `include_related` and `semantic`. `brief(query, budget,
filters)` is implemented in Stage 8 and returns `markdown`, `sections`,
`citations`, `budget`, and `used_tokens_estimate`. `explain_recall` and
`mark_status` remain placeholders until later stages.

## Mutation Policy

Agent-originated writes default to reviewable `pending` memory. The system should require explicit user review or configuration before pending memory becomes active.

Default retrieval behavior:

- Include `active`.
- Include `stale`.
- Exclude `pending` unless explicitly requested.
- Exclude `rejected`.
- Exclude `superseded` unless explicitly requested or shown as a warning.
