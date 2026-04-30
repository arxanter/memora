# Agent Instructions For Memory Workflows

Use this document when configuring Claude Code, Cursor, Codex, or another coding
agent to work with Agent Memory. Copy the relevant sections into project-level
`AGENTS.md`, `CLAUDE.md`, or `.cursor/rules/agent-memory.mdc`.

## Core Rule

Agent Memory stores and retrieves durable context. The AI agent does the
understanding work.

```text
AI agent:
  read/fetch material
  summarize and extract durable information
  call save_source or ingest_url
  call remember for atomic durable memories

Agent Memory:
  validate and store Markdown
  index and retrieve memories
  pack context under budget
  preserve citations and lifecycle state
```

## Startup Recall

At the start of substantial work, call:

```text
build_context(task, budget=1200, filters={ "project": "<project-name>" })
```

Use returned memory only when `memory_needed` is true. Preserve citations when
summarizing or making decisions from recalled memory.

## Capturing URLs And Raw Material

When the user asks to save a URL, article, notes, transcript, document, or raw
material into memory:

1. Fetch or read the material with the agent's normal browser/file tools.
2. Produce a concise extract.
3. Call `ingest_url` for URL-centered material or `save_source` for arbitrary
   source material.
4. Call `remember` for each durable atomic memory extracted from the source.
5. Tell the user that new memories are pending review.

Do not store raw dumps as canonical memory. Raw material belongs in `Sources/`;
canonical memory belongs in `Memories/` and should be small, durable, and
reviewable.

## Extract Format

Use this shape for `extract`:

```markdown
## Summary

## Key Ideas

## Durable Facts

## Decisions

## Preferences

## Open Questions

## Relevant Quotes
```

## MCP Tool Examples

Save a URL after fetching it:

```json
{
  "tool": "ingest_url",
  "arguments": {
    "url": "https://example.com/article",
    "title": "Article title",
    "content": "Raw Markdown or readable text fetched by the agent.",
    "extract": "## Summary\n...\n\n## Durable Facts\n- ...",
    "project": "agent-memory",
    "tags": ["source", "article"]
  }
}
```

Promote a durable decision:

```json
{
  "tool": "remember",
  "arguments": {
    "memory": {
      "type": "decision",
      "text": "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache.",
      "scope": "project",
      "project": "agent-memory",
      "confidence": 0.86,
      "source": {
        "path": "Sources/2026-04-30_article-title/extract.md",
        "title": "Article title"
      },
      "tags": ["memory", "architecture"]
    }
  }
}
```

## Review Policy

Agent-created memories should stay `pending` until reviewed:

```bash
memory review
memory mark <id> --status active
memory reject <id>
memory reindex
```

Do not store secrets, temporary logs, one-off debugging traces, or unreviewed raw
dumps as canonical memory.
