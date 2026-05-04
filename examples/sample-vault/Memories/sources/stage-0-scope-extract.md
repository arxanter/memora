---
schema_version: 1
id: mem_20260429_f00bad
title: Stage 0 planning source extract
aliases:
  - mem_20260429_f00bad
  - Stage 0 scope extract
type: source_extract
scope: project
project: agent-memory
status: pending
confidence: 0.72
created_at: 2026-04-29T12:25:00+02:00
updated_at: 2026-04-29T12:25:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/source.md
  title: Stage 0 planning source
source_links:
  - "[[Sources/2026-04-29_abcd1234/source|Stage 0 planning source]]"
author:
  kind: import
  name: Basic Memory import draft
relations:
  - type: supports
    target: mem_20260429_9f3a21
relation_links:
  - "supports: [[mem_20260429_9f3a21]]"
migration:
  from_schema_version: 0
  migrated_at: 2026-04-29T12:25:00+02:00
  tool: stage-1-sample
  notes: Demonstrates the durable migration field for imported notes.
observations:
  - category: source_extract
    text: The initial plan says Markdown is canonical and generated indexes are disposable.
tags: [memory, source, import]
---

Pending example: source extracts preserve provenance and can support canonical memories without replacing the original source note.
