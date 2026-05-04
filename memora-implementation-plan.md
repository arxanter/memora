# Local-First Memora Implementation Plan

## Goal

Build a local-first Obsidian-backed memory engine where Obsidian Markdown is the human-readable source of truth, and an agent-facing retrieval layer builds compact, relevant context on demand.

The system should:

- Store atomic memories in Obsidian-compatible Markdown.
- Index the vault into a rebuildable SQLite cache.
- Search with keyword, graph traversal, and semantic retrieval.
- Rerank, deduplicate, and pack retrieved memory under a strict token budget.
- Build concise, citation-preserving memory briefs for agents.
- Support memory lifecycle states: `pending`, `active`, `stale`, `superseded`, `rejected`.
- Sync across devices by syncing Markdown, not SQLite or embeddings.
- Provide CLI JSON commands and generated agent instructions as the first-class
  interface for coding agents.

## Competitive Baseline

Basic Memory already covers much of the generic local-first memory space:

- File-first Markdown architecture.
- Obsidian-compatible notes.
- CLI interface.
- Local indexing and reindexing.
- Semantic search with local embeddings.
- Typed observations and relations.
- Multi-project memory boundaries.
- Schema validation and repair tooling.

This project should match those baseline strengths where they are table stakes, but avoid competing as a generic note-taking AI tool.

The intended differentiation is narrower:

- Deterministic context optimization under a strict token budget.
- Coding-agent-oriented memory briefs with stable sections and citations.
- First-class lifecycle handling for stale, superseded, contradicted, and rejected memory.
- Explainable recall decisions.
- Reviewable and reversible agent-written memory.
- Import/export compatibility with existing Markdown memory systems where practical.

## Product Wedge

Position the system as a local-first context optimizer for coding agents.

Primary user value:

- Agents receive compact context instead of unstructured note dumps.
- Users can inspect exactly where recalled context came from.
- Stale or superseded knowledge is surfaced as a warning, not silently mixed into current context.
- Agent-written memory is reviewed before it becomes durable truth.

Non-goals for v1:

- A full Obsidian replacement.
- A hosted memory SaaS.
- A universal personal knowledge manager.
- Automatic ingestion from every app.
- Fully autonomous memory mutation without user review.

## Target Architecture

```text
Obsidian Vault/
  Memories/
    facts/
    preferences/
    decisions/
    projects/
    tasks/
  Sources/
    YYYY-MM-DD_hash/
      source.md
      extract.md
  Briefs/
  Profiles/
    user.md
    projects/
  Synthesis/
  .memora/
    config.yaml
    schemas/
    index.sqlite
    cache/
    embeddings/
    locks/
```

Markdown files are durable and syncable. Generated data under `.memora/` is local, disposable, and rebuildable.

Recommended `.gitignore`:

```gitignore
.memora/index.sqlite
.memora/cache/
.memora/embeddings/
.memora/locks/
```

## Stage 0: Product Scope

Estimated time: 1 day.

Decisions:

- Start with CLI JSON commands for both development and real agent usage.
- Use Obsidian Markdown as the source of truth.
- Use SQLite FTS5 as the first search backend.
- Add semantic search through a provider interface.
- Prefer local embeddings by default.
- Treat agent-generated memory as pending unless configured otherwise.

Initial memory types:

- `fact`
- `preference`
- `decision`
- `task`
- `source_extract`
- `project_context`
- `conversation_summary`

Target integrations:

- Claude Code.
- Codex.
- Cursor.
- Agents that can run shell commands and consume JSON output.

Compatibility targets:

- Import generic Markdown notes.
- Import/export Basic Memory-like observations and relations where feasible.
- Read existing project memory files such as `CLAUDE.md`, `AGENTS.md`, and Cursor rules as sources, not canonical memories.

Deliverables:

```text
docs/spec.md
docs/schema.md
docs/commands.md
docs/competitive-baseline.md
docs/evaluation.md
```

## Stage 1: Vault Schema

Estimated time: 2 days.

Example memory file:

```markdown
---
schema_version: 1
id: mem_20260429_9f3a21
type: decision
scope: project
project: memora
status: active
confidence: 0.86
created_at: 2026-04-29T12:00:00+02:00
updated_at: 2026-04-29T12:00:00+02:00
valid_from: 2026-04-29
valid_to:
source:
  path: Sources/2026-04-29_abcd1234/extract.md
supersedes: []
contradicts: []
relations:
  - type: supports
    target: mem_20260429_7ab901
observations:
  - category: decision
    text: SQLite is a disposable index, not durable state.
tags: [memory, retrieval, obsidian]
---

Use Obsidian Markdown as source of truth and SQLite only as rebuildable cache.
```

Schema rules:

- Every file has `schema_version`, `id`, `type`, `status`, `created_at`, and `updated_at`.
- Project-scoped memory must include `project`.
- Agent-generated memory must include `source` and `confidence`.
- User-authored memory can omit `confidence` and is treated as high-authority by default.
- Notes can contain typed `observations` and typed `relations`.
- Relations are directional and validated during `doctor`.
- Lifecycle fields are part of the durable Markdown record, not only the index.

Relation vocabulary:

- `supports`
- `supersedes`
- `contradicts`
- `depends_on`
- `related_to`
- `belongs_to_project`

Deliverables:

- Schema validator.
- Sample vault.
- Schema version and migration field.
- JSON Schema or Pydantic models for frontmatter.
- Compatibility notes for importing Basic Memory-style observations and relations.

## Stage 2: CLI Skeleton

Estimated time: 1-2 days.

Initial commands:

```bash
memora init <vault>
memora remember --type decision --text "..."
memora reindex
memora search "query"
memora recall "query" --budget 1200
memora brief "query" --budget 1200
memora status
memora doctor
memora import <path>
memora export --format markdown
```

Recommended stack:

- Python + Typer + Rich for fast CLI implementation.
- SQLite for local index.
- Shared service layer behind CLI commands.

Deliverables:

- CLI package.
- Config loader.
- Basic Markdown file creation.
- JSON mode for every command that agents may call.
- Tests for schema and config.

## Stage 3: Agent CLI Integration Skeleton

Estimated time: 1-2 days.

Agent-facing commands:

```text
memora remember --json
memora search --json
memora recall --json
memora brief --json
memora inspect --json
memora explain-recall --json
memora mark --json
```

Rules:

- Agent-facing commands return structured JSON plus citations.
- Agent-facing tools never mutate active memory silently unless explicitly configured.
- `remember` defaults to creating a pending/reviewable memory when called by an agent.
- CLI commands use the same validation, retrieval, and packing code.

Deliverables:

- Generated Codex, Claude Code, and Cursor setup docs.
- Golden tests for CLI JSON responses.

## Stage 4: Markdown Parser, Graph, And Indexer

Estimated time: 2-3 days.

Indexer responsibilities:

- Read Markdown files.
- Parse YAML frontmatter.
- Compute `content_hash`.
- Split documents into chunks.
- Extract typed observations and relations.
- Validate graph references.
- Write metadata, chunks, observations, and relations to SQLite.

SQLite tables:

```sql
documents(id, path, type, status, created_at, updated_at, content_hash)
chunks(id, document_id, chunk_type, text, token_estimate, content_hash)
memories(id, document_id, type, scope, project, status, confidence, valid_from, valid_to)
observations(id, document_id, category, text, confidence, content_hash)
links(from_id, to_id, relation, confidence)
chunk_fts using sqlite fts5
```

Chunking rules:

- `Memories/*.md`: one main chunk plus optional section chunks.
- `extract.md`: `Summary`, `Executive Summary`, each `Key Idea`, `Connections`, `Raw Quotes`.
- `source.md`: excluded by default or indexed with lower priority.
- `observations`: indexed as first-class atomic recall units.
- `relations`: indexed for graph expansion and citation context.

Deliverables:

- `memora reindex`.
- Incremental indexing by content hash.
- Unchanged chunk skip logic.
- SQLite FTS5 keyword search.
- Graph validation and orphan detection.
- Optional file watcher as stretch goal.

## Stage 5: Retrieval V1: Keyword, Metadata, And Graph

Estimated time: 2 days.

Search scoring:

```text
score =
  fts_score
  + graph_neighbor_boost
  + memory_type_boost
  + status_boost
  + confidence_boost
  + recency_boost
  + rating_boost
  - stale_penalty
  - superseded_penalty
```

Example commands:

```bash
memora search "vector db" --project foo --type decision --status active
memora search "preferences" --scope user
memora search "agent memory" --include-related
```

Deliverables:

- Ranked search results.
- Project, type, status, date filters.
- Snippets.
- Citations with Obsidian paths.
- Optional related-memory expansion.
- Deterministic scoring fixtures.

## Stage 6: Semantic Search

Estimated time: 2-4 days.

Embedding constraints:

- Production embeddings must come from the same AI model/session that the user
  is interacting with.
- Do not add first-class local, public/open, or separate API embedding providers
  as the default project path.
- Keep a pluggable provider interface so a future agent/client bridge can inject
  same-session embeddings.
- Preserve deterministic embeddings only for tests and fixtures.

Data model:

```sql
embeddings(chunk_id, model, vector, content_hash)
```

Retrieval flow:

```text
query
-> FTS top 100
-> graph-expanded candidates
-> vector top 100
-> merge candidates
-> normalize scores
-> top 50
```

Rules:

- FTS-only recall must work well before semantic search is enabled.
- Semantic search is enabled by provider config, not hardcoded.
- Same-session embeddings are the only production recommendation; no separate
  local/open/API embedding provider should be presented as the default path.
- Embeddings are cache data and can be rebuilt from Markdown.

Deliverables:

- Embedding provider abstraction.
- Lazy embedding generation.
- Hybrid retrieval.
- Embedding cache invalidation by `content_hash`.
- Same-session embedding integration notes and safe fallback docs.

## Stage 7: Rerank And Token Budget Packing

Estimated time: 2-3 days.

Pipeline:

```text
candidates
-> remove inactive/rejected unless explicitly requested
-> resolve superseded links
-> dedupe near-identical chunks
-> rerank by relevance + metadata
-> diversify by source
-> enforce per-category caps
-> pack until budget is exhausted
```

Example command:

```bash
memora recall "agent memory" --budget 1500
```

Expected JSON output:

```json
{
  "budget": 1500,
  "used_tokens_estimate": 1180,
  "chunks": [],
  "citations": []
}
```

Deliverables:

- Token estimator.
- Max tokens per chunk.
- Diversity cap per document.
- Caps by memory type and project.
- Citation object for every packed chunk.
- Deterministic packing tests.
- Regression tests proving output never exceeds budget.

## Stage 8: Memora Brief

Estimated time: 2-4 days.

This is the main agent-facing feature.

Example command:

```bash
memora brief "What did we decide about Obsidian memory sync?" --budget 1200
```

Expected output:

```markdown
## Memora Brief

Current relevant facts:
- Obsidian Markdown is the durable source of truth.
- SQLite/vector indexes are disposable local caches.

Current decisions:
- Do not sync SQLite across devices.
- Rebuild index with `memora reindex`.

Warnings:
- Earlier idea to store memory only in SQLite is superseded.

Open questions:
- Embeddings provider is not finalized.

Citations:
- Memories/decisions/mem_20260429_9f3a21.md
```

Implementation approach:

- Start with deterministic brief generation from selected chunks.
- Add optional LLM summarization later.
- Preserve citations.
- Enforce strict token budget.
- Keep stale and superseded memory out of the main sections.
- Include stale/superseded memory only in a warning section when relevant.
- Include open questions and conflicts when the graph detects them.

Deliverables:

- `memora brief`.
- Citation-preserving brief format.
- Strict budget mode.
- JSON and Markdown output modes.
- Golden fixtures for brief formatting.

## Stage 9: Lifecycle: Supersede, Contradict, Decay

Estimated time: 3-5 days.

Commands:

```bash
memora supersede <old_id> --by <new_id>
memora contradict <id1> <id2>
memora mark <id> --status stale
memora decay
memora review
memora reject <id>
```

Rules:

- Agent-created memories start as `pending` unless config opts into direct writes.
- `pending` is never used in recall unless explicitly requested.
- `rejected` is never used by default.
- `superseded` is hidden unless explicitly requested.
- `stale` can appear in warning sections.
- `valid_to` reduces ranking score.
- `last_used_at` updates after recall.
- Superseding a memory creates a relation and updates status atomically.
- Contradictions are surfaced in `brief` and `doctor`.

Deliverables:

- Lifecycle commands.
- Retrieval respects lifecycle.
- `doctor` detects contradictions and missing links.
- Review queue for agent-generated memory.
- Audit trail in Markdown frontmatter or sidecar history.

## Stage 10: Automatic Recall Policy

Estimated time: 2-4 days.

Add a lightweight classifier that decides whether a user request needs memory.

Recall triggers:

- User asks what was previously decided.
- User references earlier work.
- User asks about preferences.
- User asks project-specific questions.
- User asks for history or status.

Agent-facing commands:

```bash
memora should-recall "<user message>"
memora brief "<user message>"
```

Agent-facing commands:

```text
memora should-recall "<message>" --json
memora brief "<query>" --json
memora build-context "<task>" --json
memora remember ... --json
memora supersede ... --json
```

Deliverables:

- Recall policy.
- Claude Code and Codex usage docs.
- False-positive and false-negative recall tests.

## Stage 11: Sync Model

Estimated time: 1-2 days.

Sync rules:

- Markdown syncs.
- SQLite index does not need to sync.
- Embeddings do not need to sync.
- `memora reindex` restores local working state.
- Writes to Markdown are atomic.
- Watcher, reindex, and agent writes use locks.

Commands:

```bash
memora doctor
memora reindex --clean
memora conflicts
```

Deliverables:

- Sync guide.
- Conflict detection.
- Rebuildable index workflow.
- Atomic write strategy.
- Locking strategy.

## Stage 12: Tests, Evaluation, And Fixtures

Estimated time: 4-6 days.

Test areas:

- YAML parsing.
- Schema migration.
- Chunking.
- FTS retrieval.
- Semantic merge.
- Graph relation expansion.
- Budget packing.
- Superseded filtering.
- Brief citations.
- Reindex idempotency.
- Sync and rebuild from fixture vault.
- Recall explanations.
- Agent write review flow.
- Import/export compatibility.

Fixtures:

```text
tests/fixtures/vault-basic
tests/fixtures/vault-conflicts
tests/fixtures/vault-large
tests/fixtures/vault-basic-memory-import
```

Evaluation set:

- 30-50 representative coding-agent questions.
- Expected included memory IDs.
- Expected excluded memory IDs.
- Expected warning/conflict behavior.
- Expected maximum token budget.

## Stage 13: UX Polish

Estimated time: 2-4 days.

Important commands:

```bash
memora status
memora inspect <id>
memora open <id>
memora explain-recall "query"
memora review
memora graph <id>
```

`explain-recall` should explain why chunks were selected or skipped:

```text
Selected chunk A because semantic score 0.82, active decision, project match.
Skipped chunk B because superseded by mem_x.
Skipped chunk C because over budget.
```

Deliverables:

- Readable CLI output.
- JSON mode for agents.
- Human mode for terminal.
- Diff-style review for pending memories.
- Link to open the source Markdown file in Obsidian when available.

## Timeline

Prototype:

```text
4-6 days
- Markdown schema
- CLI
- indexer
- FTS search
- simple recall
```

MVP:

```text
10-15 working days
- typed observations and relations
- metadata filters
- graph expansion
- budget packing
- memora brief
- lifecycle statuses
- rebuildable index
- pending memora review
```

Competitive v1:

```text
4-6 weeks
- semantic search
- rerank
- contradiction/supersede tools
- automatic recall policy
- import/export compatibility
- recall evaluation suite
```

Production-grade personal system:

```text
8-12 weeks
- robust importers
- background watcher
- migrations
- conflict resolution
- multi-vault support
- observability
- polished agent integrations
```

## Recommended First Milestone

Build a Basic Memory-aware MVP without embeddings, but with the parts that differentiate this project.

Reason: FTS plus good chunking, graph relations, budget packing, lifecycle handling, and memora brief already gives most of the practical value. Semantic search can be added after schema and lifecycle behavior stabilize.

Implementation order:

1. Schema and sample vault.
2. CLI: `init`, `remember`, `reindex`, `search`.
3. Agent-facing CLI JSON commands: `search`, `recall`, `brief`.
4. Chunking for extracts, memories, observations, and relations.
5. Recall with strict token budget.
6. Memory brief with citations.
7. Lifecycle statuses and pending review.
8. Basic Memory import/export compatibility spike.
9. Semantic search.
10. Automatic recall policy.

