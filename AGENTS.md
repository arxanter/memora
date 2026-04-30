## Agent Memory Usage

This project uses Agent Memory via MCP.

At the start of substantial work, call:

`build_context(task, budget=1200, filters={ "project": "<project-name>" })`

Use returned memory only when `memory_needed` is true.

## Capturing New Material

When the user asks to save a URL, article, notes, transcript, document, or raw material into memory:

1. Read or fetch the source material.
2. Do not create local `Sources/...` files unless the user explicitly asks for local files.
3. Create a concise extract from the material.
4. Save the extract through Agent Memory using `remember(memory)` with type `source_extract`.
5. The `source_extract` should include:
   - Source URL or origin
   - Short summary
   - Key ideas
   - Durable facts
   - Decisions, if any
   - User preferences, if any
   - Open questions
   - Relevant quotes
6. Do not store raw dumps as canonical memory.
7. Promote only durable, atomic facts, decisions, preferences, tasks, or project context into separate Agent Memory items when useful.
8. Agent-created memories should remain `pending` for review.

Do not expect Agent Memory to fetch or analyze URLs by itself. The AI agent is
responsible for reading the material, producing the extract, and deciding which
facts/decisions/preferences are durable enough to remember.

Canonical memories should be small and atomic:

- `fact`: stable factual knowledge
- `decision`: project or architecture decision
- `preference`: user preference
- `project_context`: durable project background
- `task`: open follow-up or question
- `source_extract`: summary of imported source material

Example source capture:

```json
{
  "type": "source_extract",
  "title": "Article title",
  "source_url": "https://example.com/article",
  "content": "Summary, key ideas, durable facts, decisions, preferences, open questions, and relevant quotes.",
  "project": "agent-memory",
  "tags": ["source", "article"]
}
```

Example durable memory after extraction:

```json
{
  "type": "decision",
  "text": "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache.",
  "scope": "project",
  "project": "agent-memory",
  "confidence": 0.86,
  "source": {
    "url": "https://example.com/article",
    "title": "Article title"
  },
  "tags": ["memory", "architecture"]
}
```
