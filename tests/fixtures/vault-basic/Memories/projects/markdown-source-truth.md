---
schema_version: 1
id: mem_20260430_arch_markdown
type: project_context
scope: project
project: memora
status: active
confidence: 0.95
created_at: 2026-04-30T09:05:00+02:00
updated_at: 2026-04-30T09:05:00+02:00
valid_from: 2026-04-30
valid_to:
supersedes: []
contradicts: []
relations: []
observations:
  - category: architecture
    text: Markdown in the managed vault is the durable source of truth.
    confidence: 0.95
tags: [architecture, markdown]
---

Markdown in the managed vault is the durable source of truth for Memora.
The local database and embedding files are disposable generated state.
