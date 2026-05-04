---
schema_version: 1
id: mem_20260429_d4e5f6
title: Add CLI skeleton
aliases:
  - mem_20260429_d4e5f6
  - Pending CLI skeleton task
type: task
scope: project
project: memora
status: pending
confidence: 0.78
created_at: 2026-04-29T12:20:00+02:00
updated_at: 2026-04-29T12:20:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
  title: Stage 0 planning extract
source_links:
  - "[[Sources/2026-04-29_abcd1234/extract|Stage 0 planning extract]]"
author:
  kind: agent
  name: Cursor
relations:
  - type: depends_on
    target: mem_20260429_9f3a21
  - type: belongs_to_project
    target: mem_20260429_c0ffee
relation_links:
  - "depends_on: [[mem_20260429_9f3a21]]"
  - "belongs_to_project: [[mem_20260429_c0ffee]]"
observations:
  - category: task
    text: Add a CLI skeleton after the schema validator exists.
    confidence: 0.78
tags: [memory, cli, example]
---

Pending example: keep the CLI skeleton aligned with the current public command surface.
