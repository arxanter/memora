# Memora Architecture

Memora is a CLI-first local memory core backed by an Obsidian-compatible vault.
The durable data model has three layers:

```text
raw/      staging for unprocessed input and sidecar metadata
Sources/  curated durable source evidence plus optional extracts
Memories/ atomic durable facts, decisions, preferences, tasks, and context
```

`.memora/` stores config plus rebuildable local state such as SQLite indexes,
embedding cache, locks, and schemas.

## Command Surface

The public CLI is intentionally small:

- Vault basics: `init`, `setup`, `status`, `doctor`, `reindex`.
- Raw staging: `raw add`, `raw list`, `raw inspect`.
- Curated evidence: `source add`, `lookup-source`.
- Memory writes and review: `remember`, `review`, `review approve`,
  `review reject`.
- Retrieval: `search`, `recall`, `brief`, `build-context`, `inspect`, `open`.
- Agent integration: `agent rules`, `agent integrate`, `agent update`,
  `agent status`, `agent-aliases list`, `agent-aliases set`.
- Session capture: `session finalize`.

Older direct import, scheduled ingest, lifecycle, profile, synthesis, and eval
commands are compatibility/internal paths and are not part of the public product
surface.

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
prefer the durable atomic types.

Agent-created memories default to `pending` unless policy allows activation.
Review commands approve or reject pending memories with audit history.

## Indexing And Recall

The SQLite index is disposable local state rebuilt from Markdown with
`memora reindex`. Search and recall read from the index and can refresh it before
queries according to `index_freshness` config.

`build-context` first applies recall policy from `recall_policy.py`. If memory is
not needed, it returns `memory_needed=false` and no context. When memory is
needed, it packs a citation-preserving brief under a task-class budget.

`agent_policy.enabled=false` disables agent memory use. `auto_recall=false`
keeps memory writable/searchable but makes `build-context` skip automatic recall.

## Agent Rules

Generated rules are the contract for Cursor, Claude, Codex, and generic
`AGENTS.md` consumers. They instruct agents to:

- treat `Remi`, `Рэми`, and `Реми` aliases as explicit Memora triggers;
- call `memora build-context ... --json` only when memory is relevant;
- use returned context only when `memory_needed=true`;
- stage raw input with `raw add`;
- save durable evidence with `source add`;
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
