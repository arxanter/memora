# Schema Direction

## Canonical Storage

Markdown files in the managed vault are the durable source of truth. Each memory
is a Markdown document with YAML frontmatter plus human-readable body content.
The SQLite database is only a rebuildable cache derived from these files.

Recommended managed layout:

```text
memora/
  engine/
  vault/
    raw/
      inbox/
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
  config.yaml
  state/
    index.sqlite
    cache/
    embeddings/
    locks/
  venv/
```

Generated paths under `state/` are local and disposable. They should be
rebuildable from Markdown.

`raw/` is the unprocessed input layer. Users, exports, and
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
type: decision
scope: project
project: memora
status: active
confidence: 0.86
created_at: 2026-04-29T12:00:00+02:00
updated_at: 2026-04-29T12:00:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
  title: Stage 0 planning extract
author:
  kind: agent
  name: Cursor
relations:
  - type: supports
    target: mem_20260429_7ab901
tags: [memory, retrieval, markdown]
history:
  - at: 2026-04-30T12:15:00+02:00
    action: update
    actor: memora
    from_status: active
    to_status: active
    reason: normalized schema metadata
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
- Legacy fields such as `title`, `aliases`, `source_links`, `relation_links`,
  `supersedes`, `contradicts`, and `observations` may still be parsed from older
  vaults. New writes should not persist generated presentation metadata or a
  duplicate observation that repeats the memory body.

## Supported Types

Initial memory types:

- `fact`
- `preference`
- `decision`
- `task`
- `project_context`
- `conversation_summary`

`source_extract` is legacy-read-only for existing imported memories. New source
evidence belongs in `Sources/`, and durable source-backed analyses belong in
`Wiki/syntheses/`.

Initial lifecycle statuses:

- `pending`
- `active`
- `stale`
- `superseded`
- `rejected`

## Relations

Memories may contain typed relations. Relations are directional graph edges and
should be validated during `memora doctor`.

Initial relation vocabulary:

- `supports`
- `supersedes`
- `contradicts`
- `depends_on`
- `related_to`

Lifecycle links such as supersession and contradiction belong in durable Markdown, not only in SQLite.

Lifecycle behavior stores supersession and contradiction links in `relations[]`.
The indexer still projects old top-level `supersedes` and `contradicts` lists for
legacy files, but new writes should use the single relation representation.

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
It is populated from memory bodies, heading section chunks, and legacy
observations when present. Optional semantic search uses the `embeddings` table.

Graph validation checks relation targets from `relations` plus legacy
`supersedes` and `contradicts` against known memory IDs. `memora doctor` reports
orphan targets as graph issues.

Embeddings are cache data keyed by chunk, model, and `content_hash`. If a chunk
changes, lazy semantic search refreshes the stale vector. The vectors can be
deleted and rebuilt from Markdown plus the configured embedding provider.

## Compatibility Notes

Basic Memory-like observations and relations are legacy import input:

- Basic Memory observations should be folded into the memory body when they
  become durable memory. Existing imported `observations[]` remain readable.
- Directional Basic Memory relations become `relations[]` entries when the
  relation type fits the Stage 1 vocabulary: `supports`, `supersedes`,
  `contradicts`, `depends_on`, or `related_to`.
- Unsupported relation names should be preserved as source material during
  import until a later migration maps or rejects them explicitly.
- Imported project memory files such as `CLAUDE.md`, `AGENTS.md`, and Cursor
  rules should be represented as `Sources/` material unless a user promotes
  extracted items into canonical memories.
