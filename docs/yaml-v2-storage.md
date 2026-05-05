# YAML v2 Storage Direction

This is the post-cleanup target shape for a smaller canonical storage format.
It is a design target, not an in-place migration contract.

## Memories

Memories are good YAML-only records because each one should be a small atomic
claim. The v2 shape should replace Markdown body plus duplicate observations
with one canonical `text` field.

```yaml
schema_version: 2
id: mem_20260505_example
type: decision
scope: project
project: memory-project
status: active
created_at: 2026-05-05T19:00:00+02:00
updated_at: 2026-05-05T19:00:00+02:00
text: Use relations[] as the single graph representation.
confidence: 0.9
source:
  path: Sources/2026-05-05_cleanup/extract.md
  title: Schema cleanup notes
author:
  kind: agent
  name: Cursor
relations:
  - type: supersedes
    target: mem_20260501_old_graph_shape
tags:
  - schema
  - cleanup
risk_flags: []
history:
  - at: 2026-05-05T19:05:00+02:00
    action: approve
    actor: memora
    from_status: pending
    to_status: active
```

Rules:

- `text` is the canonical recall/indexing unit. Do not generate an
  `observations[]` entry that repeats it.
- `relations[]` is the only graph representation. Do not write top-level
  `supersedes` or `contradicts`.
- Omit empty/default fields on write. Keep legacy reads tolerant during
  migration.
- Keep `valid_to` only for explicit expiry/decay behavior. Do not write
  `valid_from` by default.

## Sources

Sources should not become one large YAML blob because source and extract content
can be long Markdown or text. The cleaner v2 layout is a directory with compact
metadata plus separate content files.

```text
Sources/2026-05-05_cleanup/
  meta.yaml
  source.md
  extract.md
```

```yaml
schema_version: 2
source_id: 2026-05-05_cleanup
title: Schema cleanup notes
url: null
captured_at: 2026-05-05T19:00:00+02:00
channel: ai_session
source_quality: agent_fetched
sensitivity: normal
risk_flags: []
tags:
  - schema
origin:
  provider: cursor
  format: markdown
files:
  source: source.md
  extract: extract.md
```

Rules:

- Do not store project metadata on sources. Project scope belongs on promoted
  memories.
- Persist compact safety metadata (`sensitivity`, `risk_flags`) and recompute
  full scanner findings when needed.
- Keep citations path-based: memories cite `Sources/<source_id>/extract.md` or
  `source.md`.

## Migration Shape

The v1-to-v2 migrator should be conservative:

- Parse legacy Markdown frontmatter and body.
- Move memory body into `text`.
- Drop generated presentation fields (`title`, `aliases`, wikilinks).
- Convert legacy top-level `supersedes` and `contradicts` into `relations[]`.
- Preserve legacy `observations[]` only when an observation contains distinct
  content that cannot be folded into `text`.
- Split Source frontmatter into `meta.yaml` while leaving `source.md` and
  `extract.md` as content files.
