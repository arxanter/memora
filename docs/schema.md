# Schema Direction

## Canonical Storage

Markdown files in the Obsidian vault are the durable source of truth. Each memory is a Markdown document with YAML frontmatter plus human-readable body content. The SQLite database is only a rebuildable cache derived from these files.

Recommended vault layout:

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
  .agent-memory/
    config.yaml
    schemas/
    index.sqlite
    cache/
    embeddings/
    locks/
```

Generated paths under `.agent-memory/` are local and disposable. They should be rebuildable from Markdown.

## Memory Frontmatter

The initial memory schema should include:

```yaml
schema_version: 1
id: mem_20260429_9f3a21
type: decision
scope: project
project: agent-memory
status: active
confidence: 0.86
created_at: 2026-04-29T12:00:00+02:00
updated_at: 2026-04-29T12:00:00+02:00
valid_from: 2026-04-29
valid_to:
source:
  path: Sources/2026-04-29_abcd1234/extract.md
author:
  kind: agent
  name: Cursor
supersedes: []
contradicts: []
relations:
  - type: supports
    target: mem_20260429_7ab901
observations:
  - category: decision
    text: SQLite is a disposable index, not durable state.
tags: [memory, retrieval, obsidian]
```

The Stage 1 implementation lives in `src/agent_memory/schema.py`. `MemoryFrontmatter`
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

## Supported Types

Initial memory types:

- `fact`
- `preference`
- `decision`
- `task`
- `source_extract`
- `project_context`
- `conversation_summary`

Initial lifecycle statuses:

- `pending`
- `active`
- `stale`
- `superseded`
- `rejected`

## Observations And Relations

Memories may contain typed observations and typed relations. Observations are atomic recall units. Relations are directional graph edges and should be validated during `memory doctor`.

Initial relation vocabulary:

- `supports`
- `supersedes`
- `contradicts`
- `depends_on`
- `related_to`
- `belongs_to_project`

Lifecycle links such as supersession and contradiction belong in durable Markdown, not only in SQLite.

## SQLite Cache

The index should be rebuildable from Markdown and may initially contain:

```sql
documents(id, path, type, status, created_at, updated_at, content_hash)
chunks(id, document_id, chunk_type, text, token_estimate, content_hash)
memories(id, document_id, type, scope, project, status, confidence, valid_from, valid_to)
observations(id, document_id, category, text, confidence, content_hash)
links(from_id, to_id, relation, confidence)
chunk_fts using sqlite fts5
```

Embeddings, when added, are cache data keyed by chunk and content hash:

```sql
embeddings(chunk_id, model, vector, content_hash)
```

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
