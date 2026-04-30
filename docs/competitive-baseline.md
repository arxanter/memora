# Competitive Baseline

## Reference Baseline

Basic Memory already covers much of the generic local-first AI memory space:

- File-first Markdown architecture.
- Obsidian-compatible notes.
- CLI and MCP interfaces.
- Local indexing and reindexing.
- Semantic search with local embeddings.
- Typed observations and relations.
- Multi-project memory boundaries.
- Schema validation and repair tooling.

This project should treat those capabilities as table stakes where they are relevant, while keeping the product narrower and more opinionated for coding-agent context.

## Capabilities To Match

The project should support:

- Markdown-first durable storage.
- Obsidian-compatible vault layout.
- CLI and MCP access.
- Local rebuildable indexing.
- Import and export paths for observations and relations where feasible.
- Project-scoped memory.
- Validation and repair tooling.
- A path to local semantic search through a provider interface.

## Differentiation

The product should compete primarily on context quality for coding agents:

- Deterministic packing under a strict token budget.
- Stable memory brief format optimized for agent prompts.
- Citation-preserving recall and brief generation.
- Lifecycle-aware retrieval that handles stale, superseded, contradicted, pending, and rejected memory explicitly.
- Explainable recall decisions.
- Reviewable and reversible agent-written memory.

The most important output is not a search result list. It is a compact, trustworthy context bundle that can be inserted into an agent conversation.

## Compatibility Strategy

Compatibility should be pragmatic:

- Import generic Markdown as source material.
- Map Basic Memory-like observations to typed `observations`.
- Map Basic Memory-like relations to typed `relations`.
- Preserve source provenance during import.
- Export Markdown and relation data in a shape that other local memory tools can consume when practical.

Compatibility should not force the internal product model to become a clone of Basic Memory.

## Positioning

Recommended positioning:

> A local-first memory brief engine for coding agents, backed by Obsidian Markdown.

This keeps the project distinct from generic note-taking AI tools and focuses the implementation on the user-facing gap: agents need compact, current, cited context with lifecycle-aware warnings.
