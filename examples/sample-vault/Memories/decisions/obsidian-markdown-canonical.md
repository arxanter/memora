---
schema_version: 1
id: mem_20260429_9f3a21
title: Use Obsidian Markdown as source of truth
aliases:
  - mem_20260429_9f3a21
  - Markdown as durable memory
type: decision
scope: project
project: agent-memory
status: active
confidence: 0.86
created_at: 2026-04-29T12:15:00+02:00
updated_at: 2026-04-29T12:15:00+02:00
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
  - type: related_to
    target: mem_20260429_ab12cd
  - type: belongs_to_project
    target: mem_20260429_c0ffee
relation_links:
  - "supports: [[mem_20260429_7ab901]]"
  - "related_to: [[mem_20260429_ab12cd]]"
  - "belongs_to_project: [[mem_20260429_c0ffee]]"
observations:
  - category: decision
    text: Obsidian-compatible Markdown is the durable source of truth.
    confidence: 0.86
tags: [memory, retrieval, obsidian]
---

Use Obsidian Markdown as source of truth and SQLite only as a rebuildable cache.
