# Memora Architecture

Memora is a CLI-first local memory core backed by an Obsidian-compatible vault.
The durable data model has four persistent layers:

```text
raw/      staging for unprocessed input and sidecar metadata
Sources/  curated durable source evidence plus optional extracts
Memories/ atomic durable facts, decisions, preferences, tasks, and context
Wiki/     maintained overviews, entities, concepts, source notes, and syntheses
```

`.memora/` stores config plus rebuildable local state such as SQLite indexes,
embedding cache, locks, and schemas.

## Command Surface

The public CLI is intentionally small:

- Vault basics: `init`, `setup`, `status`, `doctor`, `conflicts`, `reindex`.
- Raw staging: `raw add`, `raw list`, `raw inspect`.
- Curated evidence: `source add`, `lookup-source`.
- Wiki maintenance: `wiki status`, `wiki read`, `wiki search`, `wiki ingest`,
  `wiki synthesize`, `wiki lint`.
- Memory writes and review: `remember`, `review`, `review approve`,
  `review reject`.
- Retrieval: `search`, `context`, `recall`, `brief`, `build-context`,
  `inspect`, `open`.
- Agent integration: `agent rules`, `agent integrate`, `agent update`,
  `agent status`, `agent-aliases list`, `agent-aliases set`.
- Session capture: `session finalize`.

`build-context` may include a bounded profile-style rollup generated in memory.
Lifecycle changes are handled through `review approve` and `review reject`.

For a compact option-level reference intended for agents, see
`docs/cli-agent-reference.md`.

## Raw Staging

`memora raw add <path> --kind pdf|zoom|slack|text --format pdf|markdown|json|txt`
copies input to `raw/inbox/<kind>/` and writes `<file>.meta.json`. It does not
extract text, create `Sources/`, or promote memories.

Raw metadata includes the source kind, format, title, project, tags,
sensitivity, capture time, original path, file name, byte size, and content
hash. `raw inspect` returns this metadata and a preview when the staged file is
readable text.

`memora raw list` defaults to `raw/inbox` so agents review only unprocessed
material. After `source add` saves curated evidence, agents should call
`memora raw mark-processed <path> --source-id <source_id>` to move the raw file
and sidecar metadata into `raw/processed`.

## Curated Sources

`memora source add <source.md> --extract <extract.md>` saves durable evidence
under `Sources/<source_id>/source.md` and, when provided, `extract.md`.

`Sources/` is the citation layer. It stores normalized source text, an optional
agent-authored extract, metadata (`kind`, `format`, `project`, `tags`,
`sensitivity`, origin), safety scan results, and links between source and
extract. `lookup-source` reads from this layer when an agent needs compact
evidence.

## Canonical Memories

`Memories/` stores small atomic claims as Markdown with YAML frontmatter. Memory
types include `fact`, `decision`, `preference`, `task`, `project_context`,
`source_extract`, and `conversation_summary`; the public write path should
prefer the durable atomic types. New `project_context` files are written under
`Memories/context/`. `source_extract` is compatibility-only; source evidence
belongs in `Sources/`, and durable source-backed analyses belong in
`Wiki/syntheses/`.

Agent-created memories default to `pending` unless policy allows activation.
Review commands approve or reject pending memories with audit history.

## Wiki Layer

`Wiki/` is the LLM-maintained compounding knowledge layer inspired by the LLM
Wiki pattern. It contains `index.md`, `log.md`, `overview.md`, `sources/`,
`entities/`, `concepts/`, and `syntheses/`. It is optimized for browsing,
navigation, and saved analysis, not for authoritative operational memory.

Authority order is `raw/` -> `Sources/` -> `Memories/` -> `Wiki/`. If a wiki
page contradicts an active memory, the memory wins and the wiki page should be
treated as stale. `memora context` routes bounded queries across `Memories/`,
`Wiki/`, and `Sources/` without loading all matching content into the agent
context.

## Indexing And Recall

The SQLite index is disposable local state rebuilt from Markdown with
`memora reindex`. Search and recall read from the index and can refresh it before
queries according to `index_freshness` config.

`build-context` first applies recall policy from `recall_policy.py`. If the
trigger policy does not request memory, it runs a cheap data probe against the
index: keyword search first, then local semantic search when configured and
available. If neither policy nor probe finds a strong signal, it returns
`memory_needed=false` and no context. When memory is needed, it packs a
citation-preserving brief under a task-class budget.
`memora brief` is ephemeral stdout/JSON output only; persistent saved briefs
should be stored as `Wiki/syntheses/`.

`agent_policy.enabled=false` disables agent memory use. `auto_recall=false`
keeps memory writable/searchable but makes `build-context` skip automatic recall.

## Agent Rules

Generated rules are the contract for Cursor, Claude, Codex, and generic
`AGENTS.md` consumers. They instruct agents to:

- treat `Remi`, `Рэми`, and `Реми` aliases as explicit Memora triggers;
- call `memora build-context ...` only when memory is relevant, using the compact
  default output for recall;
- use returned context only when `memory_needed=true`;
- stage raw input with `raw add`;
- save durable evidence with `source add`;
- maintain wiki pages with `wiki ingest`, `wiki synthesize`, and `wiki lint`;
- create only small atomic memories with `remember`;
- review pending memory with `review`, `review approve`, and `review reject`;
- avoid direct vault edits.

## Session And Scheduled Work

`session finalize` is the explicit session-end capture path. It saves transcript
source material, optional extract/summary, and proposed memories for review.

Scheduled agents are documentation and rules, not a separate daemon surface.
They should fetch bounded external material with their own tools, call `raw add`,
produce an extract, call `source add`, then save durable atomic memories with
`remember`.
