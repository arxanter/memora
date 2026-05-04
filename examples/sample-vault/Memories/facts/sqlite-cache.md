---
schema_version: 1
id: mem_20260429_7ab901
title: SQLite is rebuildable cache
aliases:
  - mem_20260429_7ab901
  - SQLite cache memory
type: fact
scope: project
project: memora
status: active
confidence: 0.91
created_at: 2026-04-29T12:05:00+02:00
updated_at: 2026-04-29T12:05:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
  title: Stage 0 planning extract
source_links:
  - "[[Sources/2026-04-29_abcd1234/extract|Stage 0 planning extract]]"
author:
  kind: agent
  name: Cursor
relations:
  - type: belongs_to_project
    target: mem_20260429_c0ffee
relation_links:
  - "belongs_to_project: [[mem_20260429_c0ffee]]"
observations:
  - category: fact
    text: SQLite is a disposable index and not durable state.
    confidence: 0.91
tags: [memory, sqlite, index]
---

SQLite should be treated as a rebuildable cache derived from Markdown files in the vault.
