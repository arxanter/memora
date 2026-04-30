# Command Surface

## Principles

The CLI is the development and maintenance interface. MCP is the primary coding-agent interface. Both should call the same underlying services for validation, retrieval, lifecycle handling, and token-budget packing.

Agent-facing operations should support structured JSON responses, stable error codes, and citations. Mutating commands must not silently promote agent-written memory to active durable truth unless explicitly configured.

## Initial CLI Commands

```bash
memory init <vault>
memory help
memory mcp-config
memory remember --type decision --text "..."
memory reindex
memory search "query"
memory recall "query" --budget 1200
memory explain-recall "query" --budget 1200
memory brief "query" --budget 1200
memory should-recall "user message"
memory eval <fixture-or-file>
memory status
memory inspect <id>
memory open <id>
memory graph <id>
memory doctor
memory conflicts
memory supersede <old_id> --by <new_id>
memory contradict <id1> <id2>
memory mark <id> --status stale
memory decay
memory review
memory reject <id>
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

### `memory help`

Prints a grouped overview of available Agent Memory commands with short
descriptions. Use it when you want the project-specific help surface instead of
Typer's generated `memory --help` output.

Examples:

```bash
memory help
memory help --json
```

The JSON output is stable enough for agent clients to inspect available command
groups and descriptions.

### `memory mcp-config`

Prints MCP client configuration for Agent Memory. This is the easiest way to
redisplay the `memory-mcp` setup snippet after installation.

Examples:

```bash
memory mcp-config
memory mcp-config --format claude
memory mcp-config --format cursor
memory mcp-config --vault ~/MemoryVault --command ~/.local/bin/memory-mcp
memory mcp-config --json
```

Human output is the JSON snippet agents expect in MCP client settings:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "/Users/you/.local/bin/memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

`--json` wraps that snippet with metadata such as selected format, command path,
and vault path. Supported formats are `generic`, `claude`, and `cursor`; the
current generated config shape is intentionally the same for all three because
they use compatible MCP server declarations.

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

Implemented in Stage 4 and expanded in Stage 11.

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
- Uses the local vault lock so reindex does not race with Agent Memory Markdown
  writes.

Use `--clean` to delete the existing SQLite file before rebuilding. This is the
recommended recovery path after syncing a vault on another machine, resolving
Markdown conflicts, or encountering a stale/corrupt local index.

Example:

```bash
memory reindex --vault ./memory-vault --json
memory reindex --vault ./memory-vault --clean
```

### `memory refresh-index`

Implemented in Stage 13.

Runs a conservative freshness check over durable vault inputs and calls
`memory reindex` only when needed. It tracks Markdown outside `.agent-memory/`,
`.agent-memory/config.yaml`, and `.agent-memory/schemas/`; generated files such
as `index.sqlite`, `cache/`, `embeddings/`, and `locks/` are ignored.

Freshness state is stored under `.agent-memory/cache/freshness-state.json`, so it
is disposable local cache. The command also refreshes the index when the SQLite
index is missing or older than tracked durable files.

Useful options:

- `--debounce <seconds>` waits for a quiet period before reindexing.
- `--clean` / `--no-clean` overrides whether the triggered reindex deletes the
  SQLite index first.
- `--json` returns structured change and reindex details.

Example:

```bash
memory refresh-index --vault ./memory-vault --json
memory refresh-index --vault ./memory-vault --debounce 1
```

### `memory search`

Implemented in Stage 5, with optional semantic retrieval added in Stage 6.

Searches the Stage 4 SQLite FTS index and, when `semantic.provider` is
configured, can lazily generate chunk embeddings for vector or hybrid retrieval.
It returns ranked memory-level results with snippets, score breakdowns, metadata,
planned query variants, attempted searches, and Obsidian-relative citations.
SQLite and embeddings remain disposable cache data; search does not silently
rebuild a missing or incomplete index. Run `memory reindex --vault <vault>` first
if search reports `index_missing`.

Natural-language queries are planned into a small deterministic variant list. The
original query is always tried first; normalized case/punctuation/slug variants
and safe stopword-dropped variants are only used as fallback when the original
query returns no results or too few strong results. Results are deduplicated at
the memory level and still capped by `--limit`.

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
- `--mode <auto|text|vector|hybrid>` to select retrieval mode
- `--semantic` or `--no-semantic` to override the config for one query
- `--limit <n>`

`--mode auto` is the default. It chooses `hybrid` when a semantic provider is
configured and `text` otherwise. The older `--semantic/--no-semantic` switch is
kept for compatibility: `--semantic` maps to `hybrid`, and `--no-semantic` maps
to `text`.

Scoring is deterministic for a fixed index and provider. It combines FTS rank,
optional semantic similarity, graph neighbor boost, memory type boost, status
boost, confidence boost, recency boost, rating boost, stale penalty, and
superseded penalty. Recency is calculated relative to the newest indexed result
in the candidate set, not wall-clock time.

Example:

```bash
memory search "vector db" --vault ./memory-vault --project foo --type decision --status active --json
memory search "agent memory" --vault ./memory-vault --include-related
memory search "database decisions" --vault ./memory-vault --mode auto
memory search "database decisions" --vault ./memory-vault --no-semantic
```

Semantic search is disabled by default. Under the current same-session
constraint, the standalone CLI and MCP server cannot access Cursor's active AI
session embeddings, so normal production `auto` searches should remain text
search plus deterministic query planning. If an approved same-session embedding
bridge is explicitly configured, `auto` can use hybrid search. JSON output includes
`mode`, `requested_mode`, `query_plan`, `attempted_searches`, `trace`,
`semantic.enabled`, `semantic.provider`, and `semantic.model`; see
`docs/semantic-search.md` for the current limitation, provider hook, generic
command protocol, and deterministic test-only provider.

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
- `--mode <auto|text|vector|hybrid>` to select retrieval mode
- `--semantic` or `--no-semantic` to override the config for one recall

JSON output includes a compact `retrieval` trace with planned query variants,
effective mode, semantic provider status, attempted searches, selected count, and
an empty reason when nothing was selected.

### `memory explain-recall`

Implemented in Stage 13.

Runs the same indexed retrieval and budget-packing path as `memory recall`, but
returns deterministic explanations for selected and skipped candidate chunks
without updating `last_used_at`. Human output is meant for terminal debugging;
`--json` returns structured `selected` and `skipped` arrays with reason codes,
score metadata, citations, and prose explanations.

Selected chunks explain useful signals such as keyword/semantic score, lifecycle
status, memory type, project match, graph relation, semantic provider/model, and
budget truncation.
Skipped chunks report practical reasons when available, including `superseded`,
`duplicate`, `over_budget`, `cap_filtered`, and `status_filtered`.

Example:

```bash
memory explain-recall "Obsidian sync decisions" --vault ./memory-vault
memory explain-recall "Obsidian sync decisions" --vault ./memory-vault --mode text --json
```

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
- `--mode <auto|text|vector|hybrid>` to select retrieval mode
- `--semantic` or `--no-semantic` to override the config for one brief

The rendered Markdown stays compact and does not include diagnostics. JSON output
includes the same compact `retrieval` trace exposed by recall, both top-level and
inside the recall summary.

Example:

```bash
memory brief "Obsidian sync decisions" --vault ./memory-vault --budget 1200
memory brief "Obsidian sync decisions" --vault ./memory-vault --json
```

### `memory should-recall`

Implemented in Stage 10.

Classifies a user request with deterministic heuristics and no LLM dependency.
It helps agents decide whether to call `memory brief` before answering. Human
output is concise by default, and `--json` returns the stable policy payload with
`should_recall`, `confidence`, and matched `triggers`.

Recall is recommended when a request asks about previous decisions, earlier work,
preferences, this repo/project/codebase, or project history/status. Generic
coding questions and direct commands such as `git status` should not trigger
memory.

Examples:

```bash
memory should-recall "What did we decide about embeddings?"
memory should-recall "Write a Python function that reverses a list." --json
```

Agent usage pattern:

```bash
if memory should-recall "$USER_MESSAGE" --json | jq -e '.should_recall'; then
  memory brief "$USER_MESSAGE" --budget 1200
fi
```

### `memory eval`

Implemented in Stage 12.

Runs a deterministic YAML evaluation spec, or a fixture directory containing
`evaluation.yaml`, against a temporary copy of the fixture vault. The command
performs a clean reindex first, then checks expected included IDs, excluded IDs,
warning/conflict text, token budgets, and basic explainability metadata for
search, recall, brief, review, conflict, and doctor cases.

Example:

```bash
memory eval tests/fixtures/evaluation/coding-agent-questions.yaml --json
memory eval tests/fixtures/vault-basic
```

### `memory status`

Implemented in Stage 2.

Loads config, validates canonical memory Markdown with the Stage 1 validator,
and returns a lightweight summary including memory count, pending count, issue
count, and whether the disposable SQLite index exists.

### `memory inspect`

Implemented in Stage 13.

Inspects one canonical memory by id. Human output shows type/status/scope,
absolute Markdown path, Obsidian URI, source metadata when present, and body
text. `--json` returns the same information with stable fields:
`path`, `relative_path`, `obsidian_uri`, `memory`, `body`, and `citations`.

Example:

```bash
memory inspect mem_20260430_example --vault ./memory-vault
memory inspect mem_20260430_example --vault ./memory-vault --json
```

### `memory open`

Implemented in Stage 13.

Resolves a memory id to its source Markdown file and prints both the absolute
path and an `obsidian://open?path=...` URI. By default this command has no side
effects. Pass `--launch` to invoke the system `open` command with the Obsidian
URI.

Example:

```bash
memory open mem_20260430_example --vault ./memory-vault
memory open mem_20260430_example --vault ./memory-vault --launch
```

### `memory graph`

Implemented in Stage 13.

Shows incoming and outgoing relation links for a memory id from the SQLite index.
Run `memory reindex` first if the index is missing or stale. JSON output contains
structured `incoming` and `outgoing` arrays with `from`, `to`, `relation`,
`confidence`, `direction`, and linked-memory metadata when the target exists
locally.

Example:

```bash
memory graph mem_20260430_example --vault ./memory-vault
memory graph mem_20260430_example --vault ./memory-vault --json
```

### `memory doctor`

Expanded in Stages 4, 9, and 11.

Runs schema validation across `Memories/**/*.md` and validates durable graph
references. Missing relation targets are reported as graph issues and cause the
command to exit non-zero. Recorded `contradicts` links are reported as warnings
so users can review conflicts without treating an intentional contradiction edge
as invalid Markdown. Stage 11 also includes Markdown sync conflict detection;
conflict markers and duplicate memory IDs are reported as sync issues.

### `memory conflicts`

Implemented in Stage 11.

Detects practical Markdown sync conflicts without attempting to resolve them.
The command checks syncable Markdown for conflict markers and canonical
`Memories/**/*.md` files for duplicate memory IDs and invalid frontmatter.

The command exits non-zero when conflicts are found. `--json` returns
`conflict_count`, `warning_count`, and structured issue entries with relative
paths for agent consumption.

Example:

```bash
memory conflicts --vault ./memory-vault
memory conflicts --vault ./memory-vault --json
```

### `memory mark`

Implemented in Stage 9.

Updates one memory's durable lifecycle `status` in Markdown frontmatter and
appends an audit entry under `history`. Terminal lifecycle states (`stale`,
`superseded`, and `rejected`) set `valid_to` when it is missing. Reactivating a
memory with `active` or `pending` clears `valid_to`.

Example:

```bash
memory mark mem_20260430_example --status stale --vault ./memory-vault --json
```

### `memory reject`

Implemented in Stage 9.

Marks a memory `rejected`, sets `valid_to` when missing, and records a `reject`
history entry. Rejected memory is excluded from default search, recall, and brief
generation unless `--status rejected` is passed explicitly.

### `memory supersede`

Implemented in Stage 9.

Marks `<old_id>` as `superseded`, sets its `valid_to` when missing, appends
`<old_id>` to the replacement memory's `supersedes` list, and writes audit
history entries to both Markdown files. The two file updates are prepared before
replacement so the status and relation are updated together for Stage 9 needs.

Example:

```bash
memory supersede mem_old --by mem_new --vault ./memory-vault --json
```

### `memory contradict`

Implemented in Stage 9.

Records a durable contradiction edge from the first memory to the second by
appending to `contradicts`. The target memory also receives an audit history
entry. Contradictions are shown in `memory doctor` warnings and in Memory Brief
open-question bullets when connected to recalled memory.

### `memory decay`

Implemented in Stage 9.

Scans active memories and marks any memory with an elapsed `valid_to` date as
`stale`. This is intentionally conservative: it does not infer age-based decay or
automatic recall policy.

### `memory review`

Implemented in Stage 9 and polished in Stage 13.

Lists pending agent-generated memory that needs human review. Agent-created MCP
memories still default to `pending` unless config opts into direct writes, and
pending memory remains excluded from default recall/search/brief behavior unless
requested with `--status pending`. Human output now includes a diff-style preview
of pending metadata, source, status, and body text while JSON output keeps the
stable Stage 9 review payload.

### `memory import`

Stage 2 placeholder.

The command accepts a source path and supports `--json`. Markdown and Basic
Memory-compatible import are planned for later stages.

### `memory export`

Stage 2 placeholder.

The command accepts `--format markdown` and supports `--json`. Export is planned
for a later stage.

## Local Installer And Service Commands

The project also ships local machine helper scripts:

```bash
./scripts/install.sh --vault ~/MemoryVault
agent-memory-service install
agent-memory-service start
agent-memory-service status
agent-memory-service logs
agent-memory-service restart
./scripts/uninstall.sh
```

`scripts/install.sh` creates a managed virtual environment and stable wrapper
commands for `memory`, `memory-mcp`, and `agent-memory-service`. The wrapper
commands mean users do not need to activate a venv manually after installation.

`agent-memory-service` manages a user-level maintenance service on macOS
(`launchd`) and Linux (`systemd --user`). The service is intentionally not the
stdio MCP daemon; MCP clients should launch `memory-mcp` on demand. See
`docs/local-install.md` for full setup, service, upgrade, and uninstall details.

## JSON Output

All CLI commands support `--json` so coding agents can consume stable,
structured responses. Retrieval and lifecycle commands that are not yet
implemented return `implemented: false` while preserving the intended command
signatures.

## MCP Tools

Initial MCP tools:

```text
remember(memory)
save_source(source)
save_source_with_memories(source, memories, author_name)
ingest_url(url, title, content, extract, project, tags)
search(query, filters)
recall(query, budget, filters)
brief(query, budget, filters)
should_recall(message)
build_context(task, budget, filters)
inspect(id)
explain_recall(query, budget, filters)
review()
approve(id, reason)
reject(id, reason)
mark_status(id, status)
mark_superseded(old_id, by_id, reason)
```

MCP responses should include:

- Structured JSON payloads.
- Obsidian-style path citations.
- Lifecycle status for returned memories.
- Enough scoring or selection metadata to support `explain_recall`.

`search(query, filters)` is implemented in Stage 5 and accepts the same filter
keys as the CLI using snake_case names, plus `include_related`, `mode`,
`semantic`, and `limit`.
`recall(query, budget, filters)` is implemented in Stage 7 and accepts the same
filter keys, plus `include_related`, `mode`, and `semantic`. `brief(query,
budget, filters)` is implemented in Stage 8 and returns `markdown`, `sections`,
`citations`, `budget`, `used_tokens_estimate`, and retrieval trace metadata in
JSON.

`should_recall(message)` is implemented in Stage 10 and returns the same
deterministic recall policy payload as `memory should-recall --json`.
`build_context(task, budget, filters)` applies that policy first. When recall is
recommended, it returns a Memory Brief under `brief` plus top-level `markdown`
and `citations`; when recall is not recommended, it returns `memory_needed:
false`, empty Markdown, no citations, and does not require a vault or index.
All `build_context` responses include a compact `trace` object for MCP clients:
`policy_query`, `planned_query_variants`, `mode`, `requested_mode`, semantic
status/provider/model, `attempted_searches`, `selected_count`, and `empty_reason`
when no context was selected.

`save_source(source)` and `ingest_url(url, ...)` save raw/source material under
`Sources/YYYY-MM-DD_slug/`. They intentionally do not perform AI analysis or
promote content into canonical memory. Agents should fetch/read/analyze material,
save source plus extract, then call `remember(memory)` for durable atomic facts,
decisions, preferences, project context, or tasks.

`save_source_with_memories(source, memories, author_name)` is the combined
agent workflow for already-structured material. It saves `source.md` and optional
`extract.md`, then creates only explicit atomic durable memories as `pending`.
It rejects `source_extract` memory promotion so raw summaries remain in
`Sources/` rather than canonical `Memories/`.

`explain_recall(query, budget, filters)` is implemented in Stage 13 and returns
the same structured explanation payload as `memory explain-recall --json`.
`review()` lists pending agent-generated memories for review. `approve(id,
reason)` marks a pending memory active, and `reject(id, reason)` marks it
rejected. These tools allow agents to process review queues entirely through MCP.
`mark_status(id, status)` is implemented in Stage 9 and mutates Markdown
frontmatter through the lifecycle service. `mark_superseded(old_id, by_id,
reason)` is a Stage 10 MCP wrapper around the Stage 9 supersede lifecycle
service.

## Mutation Policy

Agent-originated writes default to reviewable `pending` memory. The system should require explicit user review or configuration before pending memory becomes active.

Default retrieval behavior:

- Include `active`.
- Include `stale`.
- Exclude `pending` unless explicitly requested.
- Exclude `rejected`.
- Exclude `superseded` unless explicitly requested or shown as a warning.

Recall updates `last_used_at` in Markdown frontmatter on a best-effort basis
after chunks are packed. This field is informational metadata and is not required
for indexing correctness.
