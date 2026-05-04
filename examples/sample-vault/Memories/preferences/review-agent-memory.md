---
schema_version: 1
id: mem_20260429_ab12cd
title: Review agent-written memory
aliases:
  - mem_20260429_ab12cd
  - Reviewable agent memory
type: preference
scope: user
status: active
created_at: 2026-04-29T12:10:00+02:00
updated_at: 2026-04-29T12:10:00+02:00
author:
  kind: user
  name: Anton
relations:
  - type: related_to
    target: mem_20260429_c0ffee
relation_links:
  - "related_to: [[mem_20260429_c0ffee]]"
observations:
  - category: preference
    text: Agent-generated durable memory should stay reviewable before it becomes active truth.
tags: [memory, review]
---

Agent-written memory should be easy to inspect, reject, or promote.
