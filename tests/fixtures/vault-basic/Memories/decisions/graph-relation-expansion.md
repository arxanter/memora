---
schema_version: 1
id: mem_20260430_graph_links
type: decision
scope: project
project: memora
status: active
confidence: 0.83
created_at: 2026-04-30T09:15:00+02:00
updated_at: 2026-04-30T09:15:00+02:00
valid_from: 2026-04-30
valid_to:
supersedes: []
contradicts: []
relations:
  - type: related_to
    target: mem_20260430_arch_sqlite
    confidence: 0.8
observations:
  - category: decision
    text: Graph relation expansion should include related memories for recall context.
    confidence: 0.83
tags: [graph, retrieval]
---

Graph relation expansion should include related memories for recall context when the caller asks for connected results.
