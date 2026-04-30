## Agent Memory Usage

This project uses Agent Memory via MCP.

## Session Startup Review Check

At the beginning of each new AI session in this project, check whether there is
pending agent-created memory awaiting human review.

Preferred path:

1. Use MCP `review()` when it is available.
2. If `review()` is not exposed by the current MCP client, run
   `memory review --json`.
3. If pending items exist, summarize them in a compact review queue and ask the
   user whether to inspect, approve, reject, or defer them.
4. Do not approve or reject memory without explicit user confirmation.

For approval, use MCP `approve(id, reason)` when available, or
`mark_status(id, "active")` / `memory mark <id> --status active` otherwise. For
rejection, use MCP `reject(id, reason)` when available, or `memory reject <id>`
otherwise.

At the start of substantial work, call:

`build_context(task, budget=1200, filters={ "project": "<project-name>" })`

Use returned memory only when `memory_needed` is true.

When the user asks to find information in the knowledge base, prefer:

1. `search(query, filters)` for direct lookup and citations.
2. `recall(query, budget=1200, filters)` when the agent needs compact source
   chunks to answer a question.
3. `brief(query, budget=1200, filters)` when the user wants a synthesized,
   citation-preserving summary.

Useful filters include `project`, `type`, `status`, `scope`, `limit`,
`include_related`, and `semantic`.

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

## Reviewing Pending Memory Through MCP

Use MCP review tools when the user asks to process pending memory:

1. Call `review()` to list pending agent-generated memories.
2. Call `inspect(id)` when an item needs more detail.
3. Call `approve(id, reason)` for durable, correct memory.
4. Call `reject(id, reason)` for incorrect, transient, duplicated, or low-value memory.

Use `mark_status(id, status)` only when you need a lifecycle state other than
`active` or `rejected`, such as `stale`.

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
