# Schema Direction

## Canonical Storage

Markdown files in the Obsidian vault are the durable source of truth. Each memory is a Markdown document with YAML frontmatter plus human-readable body content. The SQLite database is only a rebuildable cache derived from these files.

Recommended vault layout:

```text
Obsidian Vault/
  raw/
    inbox/
      webclips/
      files/
      sessions/
      slack/
      zoom/
      failed/
    processed/
    quarantine/
  Memories/
    facts/
    preferences/
    decisions/
    context/
    tasks/
    conversations/
  Wiki/
    index.md
    log.md
    overview.md
    sources/
    entities/
    concepts/
    syntheses/
  Sources/
    YYYY-MM-DD_hash/
      source.md
      extract.md
  .memora/
    config.yaml
    schemas/
    index.sqlite
    cache/
    embeddings/
    locks/
```

Generated paths under `.memora/` are local and disposable. They should be rebuildable from Markdown.

`raw/` is the unprocessed input layer. Users, Obsidian Web Clipper, exports, and
future pollers can drop original material there without understanding the
canonical memory schema. Raw files are not canonical memories and should not be
loaded by default recall. Processing raw material should copy or normalize it
into `Sources/<source_id>/source.md` and optional `extract.md`, then propose
source-backed pending memories under `Memories/`. Once curated evidence is saved,
the processed raw file should be moved from `raw/inbox` to `raw/processed` with
`memora raw mark-processed` so future raw reviews stay focused on unprocessed
material.

`Wiki/` is the LLM-maintained compounding knowledge layer. It stores readable
overview, index, log, source summary, entity, concept, and synthesis pages. Wiki
pages are derived from `Sources/` and `Memories/`; they are not the authority for
current decisions or preferences. If `Wiki/` conflicts with active `Memories/`,
the wiki page is stale and should be updated through the CLI.

`memora brief` returns ephemeral citation-preserving context in stdout/JSON only.
Durable saved analyses belong in `Wiki/syntheses/`.

## Memory Frontmatter

The initial memory schema should include:

```yaml
schema_version: 1
id: mem_20260429_9f3a21
title: Use Markdown as durable memory
aliases:
  - mem_20260429_9f3a21
  - Markdown as durable memory
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
  title: Stage 0 planning extract
source_links:
  - "[[Sources/2026-04-29_abcd1234/extract|Stage 0 planning extract]]"
author:
  kind: agent
  name: Cursor
supersedes: []
contradicts: []
relations:
  - type: supports
    target: mem_20260429_7ab901
relation_links:
  - "supports: [[mem_20260429_7ab901]]"
observations:
  - category: decision
    text: SQLite is a disposable index, not durable state.
tags: [memory, retrieval, obsidian]
last_used_at: 2026-04-30T12:30:00+02:00
history:
  - at: 2026-04-30T12:15:00+02:00
    action: superseded
    actor: memora
    from_status: active
    to_status: superseded
    by: mem_20260430_replacement
```

The Stage 1 implementation lives in `src/schema.py`. `MemoryFrontmatter`
is the canonical Pydantic model for YAML frontmatter, and
`parse_markdown_document`, `validate_markdown_file`, and `validate_vault` provide
callable validation helpers without introducing a CLI.

Required fields:

- `schema_version`
- `id`
- `type`
- `status`
- `created_at`
- `updated_at`

Conditional fields:

- `project` is required for project-scoped memory.
- `source` and `confidence` are required for agent-generated memory.
- User-authored memory may omit `confidence` and should be treated as high-authority by default.
- `migration` is optional and records durable schema rewrites with `from_schema_version`, `migrated_at`, optional `tool`, and optional `notes`.
- `history` is optional audit metadata. Lifecycle commands append deterministic
  entries with `at`, `action`, `actor`, `from_status`, `to_status`, and optional
  relation-specific fields such as `target`, `by`, or `reason`.
- `last_used_at` is optional metadata updated after recall on a best-effort
  basis. It does not affect schema validity or durable graph validation.

Presentation fields such as `title`, `aliases`, `source_links`, and
`relation_links` are optional Obsidian-friendly metadata. They do not replace
stable IDs, `source.path`, or structured `relations[]`; they make sample vaults
and generated notes easier to browse as a graph.

## Supported Types

Initial memory types:

- `fact`
- `preference`
- `decision`
- `task`
- `source_extract`
- `project_context`
- `conversation_summary`

`source_extract` is compatibility-only for existing imported memories. New source
evidence should be stored in `Sources/`, and durable source-backed analyses should
be saved as `Wiki/syntheses/`.

Initial lifecycle statuses:

- `pending`
- `active`
- `stale`
- `superseded`
- `rejected`

## Observations And Relations

Memories may contain typed observations and typed relations. Observations are atomic recall units. Relations are directional graph edges and should be validated during `memora doctor`.

Initial relation vocabulary:

- `supports`
- `supersedes`
- `contradicts`
- `depends_on`
- `related_to`
- `belongs_to_project`

Lifecycle links such as supersession and contradiction belong in durable Markdown, not only in SQLite.

Stage 9 lifecycle behavior stores supersession links in the top-level
`supersedes` list on the replacement memory and contradiction links in the
top-level `contradicts` list on the source memory. The indexer projects both
lists into `links`, alongside explicit `relations[]` entries, so graph traversal,
brief generation, and doctor checks all use the same relation vocabulary.

Default retrieval includes `active` and `stale` memories. It excludes `pending`,
`rejected`, and `superseded` memories unless a caller passes an explicit
`status` filter. Stale memory can still appear in warning sections, while
superseded and rejected memory are hidden by default.

## SQLite Cache

The SQLite index is rebuildable from Markdown and contains:

```sql
documents(id, path, type, status, created_at, updated_at, content_hash)
chunks(id, document_id, chunk_type, text, token_estimate, content_hash)
memories(id, document_id, type, scope, project, status, confidence, valid_from, valid_to)
observations(id, document_id, category, text, confidence, content_hash)
links(from_id, to_id, relation, confidence)
chunk_fts using sqlite fts5
embeddings(chunk_id, model, vector, content_hash)
```

The implementation lives in `src/indexer.py`. `memora reindex`
creates or refreshes these tables with stdlib `sqlite3`, using SHA-256
`content_hash` values for documents, chunks, and observations. Incremental
reindexing parses current Markdown but skips chunk, observation, and relation
rewrites for documents whose content hash is unchanged.

`chunk_fts` is the low-level SQLite FTS5 foundation for keyword retrieval.
Stage 4 populates it with body chunks, heading section chunks, and observation
chunks. Stage 6 adds optional semantic search through the `embeddings` table.

Graph validation checks relation targets from `relations`, `supersedes`, and
`contradicts` against known memory IDs. `memora doctor` reports orphan targets as
graph issues.

Embeddings are cache data keyed by chunk, model, and `content_hash`. If a chunk
changes, lazy semantic search refreshes the stale vector. The vectors can be
deleted and rebuilt from Markdown plus the configured embedding provider.

## Compatibility Notes

Basic Memory-like observations and relations should map naturally into
`observations` and `relations` where feasible:

- Basic Memory observations become `observations[]` entries with a preserved
  `category`, `text`, and optional `confidence`.
- Directional Basic Memory relations become `relations[]` entries when the
  relation type fits the Stage 1 vocabulary: `supports`, `supersedes`,
  `contradicts`, `depends_on`, `related_to`, or `belongs_to_project`.
- Unsupported relation names should be preserved as source material during
  import until a later migration maps or rejects them explicitly.
- Imported project memory files such as `CLAUDE.md`, `AGENTS.md`, and Cursor
  rules should be represented as `source_extract` material unless a user
  promotes extracted items into canonical memories.
