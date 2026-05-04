# Product Specification

## Purpose

Memora is a local-first context optimizer for coding agents. It stores durable memory as Obsidian-compatible Markdown and builds compact, citation-preserving context for agents on demand.

The product is not trying to replace Obsidian or become a hosted knowledge-management system. The durable user-owned record is Markdown; generated indexes, embeddings, and caches are disposable local artifacts.

## Product Direction

The core wedge is better agent context, not generic note taking:

- Build concise memory briefs instead of dumping raw notes into prompts.
- Preserve citations so users can inspect where every recalled fact came from.
- Respect memory lifecycle state so stale, superseded, rejected, or pending memory is not silently mixed into current context.
- Make agent-written memory reviewable before it becomes active truth.
- Keep sync simple by syncing Markdown only, then rebuilding local SQLite and embedding caches.

## Primary Interfaces

The implementation targets one primary interface:

- CLI for development, inspection, repair, imports, exports, and local workflows.

Generated agent instructions for Claude Code, Codex, Cursor, and similar tools
should call the CLI instead of relying on a separate protocol surface.

## Source Of Truth

Obsidian Markdown is canonical. A vault contains human-readable memories, sources, profiles, briefs, and synthesis notes. SQLite, FTS indexes, embedding vectors, locks, and temporary cache files are rebuildable local data under `.memora/`.

The expected sync model is:

- Sync Markdown files across machines.
- Do not sync SQLite or embeddings.
- Rebuild local state with `memora reindex`.
- Treat generated data as safe to delete.

## Initial Memory Types

The first product scope supports these memory types:

- `fact`
- `preference`
- `decision`
- `task`
- `source_extract`
- `project_context`
- `conversation_summary`

## Lifecycle States

Every memory has a lifecycle status:

- `pending`: proposed memory awaiting review.
- `active`: current memory eligible for normal recall.
- `stale`: old or lower-confidence memory that may appear as a warning.
- `superseded`: replaced by newer memory and hidden by default.
- `rejected`: explicitly not accepted and never used by default.

Agent-generated memory defaults to `pending` unless configuration explicitly opts into direct writes.

## Compatibility

The project should be compatible where practical with existing local memory conventions:

- Import generic Markdown notes.
- Import and export Basic Memory-like observations and relations where feasible.
- Read `CLAUDE.md`, `AGENTS.md`, Cursor rules, and similar project memory files as sources, not canonical memory records.

## Non-Goals For V1

- Full Obsidian replacement.
- Hosted memory SaaS.
- Universal personal knowledge manager.
- Automatic ingestion from every app.
- Fully autonomous durable memory mutation without user review.

## Success Criteria

Stage 0 is complete when the product scope, schema direction, command surface, competitive baseline, and evaluation approach are documented clearly enough to guide the first implementation stages without adding package code.
