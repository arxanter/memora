---
source_id: 2026-04-29_abcd1234
kind: extract
schema_version: 1
title: Stage 0 Planning Extract
captured_at: 2026-04-29T12:00:00+02:00
channel: manual
source_quality: user_provided
sensitivity: normal
origin:
  provider: sample
  format: markdown
---

# Stage 0 Planning Extract

## Summary

Stage 0 planning material defines Memora as a CLI-first, local-first memory
vault where durable context is stored in managed Markdown.

## Key Ideas

- Preserve raw or imported material under `Sources/`.
- Promote only small, durable facts, decisions, preferences, project context, or tasks into `Memories/`.
- Keep generated SQLite indexes, embeddings, locks, and cache files rebuildable.

## Durable Facts

- Managed Markdown is the durable source of truth.
- SQLite indexes, embeddings, locks, and cache files are rebuildable local data.

## Decisions

- Agent-written memory should remain reviewable before becoming active truth.

## Preferences

- Source-backed memories should cite the original extract or source note.

## Open Questions

- None in this sample extract.

## Relevant Quotes

- "Canonical memories should cite this file or an extracted subset rather than treating the whole note as durable memory."
