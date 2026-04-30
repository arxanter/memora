---
schema_version: 1
id: mem_20260429_7ab901
type: fact
scope: project
project: agent-memory
status: active
confidence: 0.91
created_at: 2026-04-29T12:05:00+02:00
updated_at: 2026-04-29T12:05:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
author:
  kind: agent
  name: Cursor
relations:
  - type: belongs_to_project
    target: mem_20260429_c0ffee
observations:
  - category: fact
    text: SQLite is a disposable index and not durable state.
    confidence: 0.91
tags: [memory, sqlite, index]
---

SQLite should be treated as a rebuildable cache derived from Markdown files in the vault.
