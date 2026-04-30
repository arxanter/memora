---
schema_version: 1
id: mem_20260430_arch_sqlite
type: decision
scope: project
project: agent-memory
status: active
confidence: 0.92
created_at: 2026-04-30T09:00:00+02:00
updated_at: 2026-04-30T09:00:00+02:00
valid_from: 2026-04-30
valid_to:
supersedes: [mem_20260430_old_index]
contradicts: []
relations:
  - type: supports
    target: mem_20260430_arch_markdown
    confidence: 0.9
observations:
  - category: decision
    text: Use SQLite FTS as a rebuildable cache index for keyword memory recall.
    confidence: 0.92
tags: [architecture, retrieval, sqlite]
---

# Decision

Use SQLite FTS as the rebuildable cache index for keyword memory recall.

# Rationale

Markdown remains durable storage; the SQLite index can be deleted and rebuilt after sync.
