---
schema_version: 1
id: mem_20260430_basic_import_fixture
type: source_extract
scope: user
status: active
confidence: 0.8
created_at: 2026-04-30T11:30:00+02:00
updated_at: 2026-04-30T11:30:00+02:00
valid_from: 2026-04-30
valid_to:
source:
  path: basic-memory-export.yaml
  title: Basic Memory compatibility fixture
author:
  kind: import
  name: fixture
supersedes: []
contradicts: []
relations:
  - type: supports
    target: mem_20260430_basic_related_fixture
    confidence: 0.8
observations:
  - category: decision
    text: Importers should preserve observation category and text.
    confidence: 0.82
tags: [basic-memory, import]
---

Basic Memory observations should map to Agent Memory observations while relation shape is preserved.
