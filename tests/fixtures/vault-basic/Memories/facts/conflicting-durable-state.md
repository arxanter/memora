---
schema_version: 1
id: mem_20260430_conflict_sqlite
type: fact
scope: project
project: agent-memory
status: active
confidence: 0.55
created_at: 2026-04-30T09:25:00+02:00
updated_at: 2026-04-30T09:25:00+02:00
valid_from: 2026-04-30
valid_to:
supersedes: []
contradicts: [mem_20260430_arch_markdown]
relations: []
observations:
  - category: conflict
    text: Conflict warning says SQLite is durable state, which contradicts Markdown source truth.
    confidence: 0.55
tags: [conflict, warning]
---

Conflict warning says SQLite is durable state, which contradicts the Markdown source truth decision.
