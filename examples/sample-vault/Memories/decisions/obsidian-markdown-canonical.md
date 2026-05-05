---
schema_version: 1
id: mem_20260429_9f3a21
type: decision
scope: project
project: memora
status: active
confidence: 0.86
created_at: 2026-04-29T12:15:00+02:00
updated_at: 2026-04-29T12:15:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
  title: Stage 0 planning extract
author:
  kind: agent
  name: Cursor
relations:
  - type: supports
    target: mem_20260429_7ab901
  - type: related_to
    target: mem_20260429_ab12cd
tags: [memory, retrieval, markdown]
---

Use managed Markdown as source of truth and SQLite only as a rebuildable cache.
