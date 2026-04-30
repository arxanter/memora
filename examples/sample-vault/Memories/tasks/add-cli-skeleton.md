---
schema_version: 1
id: mem_20260429_d4e5f6
type: task
scope: project
project: agent-memory
status: pending
confidence: 0.78
created_at: 2026-04-29T12:20:00+02:00
updated_at: 2026-04-29T12:20:00+02:00
source:
  path: Sources/2026-04-29_abcd1234/extract.md
author:
  kind: agent
  name: Cursor
relations:
  - type: depends_on
    target: mem_20260429_9f3a21
  - type: belongs_to_project
    target: mem_20260429_c0ffee
observations:
  - category: task
    text: Add a CLI skeleton after the schema validator exists.
    confidence: 0.78
tags: [memory, cli, stage-2]
---

Stage 2 can introduce CLI commands once Stage 1 schema validation is in place.
