## Agent Memory Usage

This project uses Agent Memory via MCP.

## Toby / Agent Memory Policy

Treat `Toby`, `Тоби`, and `tb` as explicit Agent Memory triggers. When the user
addresses Toby, classify the request as memory work and route it through the MCP
memory tools instead of treating it as a generic chat request.

The vault may define `.agent-memory/config.yaml` `agent_policy` settings:

- `manual`: ask before saving or changing lifecycle status.
- `review`: create agent-authored memories as `pending`.
- `explicit_active`: explicit user saves may become `active`; inferred memories
  remain `pending`.
- `autonomous`: Toby may write and update lifecycle status under policy, with
  source, confidence, reason, and audit history.

Use confidence consistently:

- `0.90-1.00`: explicit user instruction, direct quote, or confirmed decision.
- `0.75-0.89`: strong source-backed extraction.
- `0.55-0.74`: plausible inference that should usually stay reviewable.
- `<0.55`: ask before creating canonical memory.

## Review And Recall Policy

Do not run memory review on every turn. Check the pending review queue once at
the beginning of a new session when memory work is relevant, or when the user
asks Toby to review memory.

Preferred path:

1. Use MCP `review()` when it is available.
2. If `review()` is not exposed by the current MCP client, run
   `memory review --json`.
3. If pending items exist, summarize them in a compact review queue and ask the
   user whether to inspect, approve, reject, or defer them.
4. Do not approve or reject memory without explicit user confirmation unless the
   vault policy is `autonomous` and the change is source-backed with a reason.

For approval, use MCP `approve(id, reason)` when available, or
`mark_status(id, "active")` / `memory mark <id> --status active` otherwise. For
rejection, use MCP `reject(id, reason)` when available, or `memory reject <id>`
otherwise.

Do not run `build_context()` for generic coding or shell tasks. For normal user
requests, first decide whether memory is relevant. Recall is relevant when the
request uses a Toby alias, asks about previous decisions, earlier work, stored
preferences, project history/status, or project-specific memory.

When recall is relevant, call:

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
2. Create a concise extract from the material.
3. Preserve the raw source and extract with `save_source`, `ingest_url`, or
   `save_source_with_memories` so the material lives under `Sources/...`.
4. Promote only durable atomic facts, decisions, preferences, tasks, or project
   context into canonical `Memories/...` items.
5. Leave inferred agent-created memories `pending` for review unless policy
   explicitly allows activation.
6. The source extract should include:
   - Source URL or origin
   - Short summary
   - Key ideas
   - Durable facts
   - Decisions, if any
   - User preferences, if any
   - Open questions
   - Relevant quotes
7. Do not store raw dumps as canonical memory.

Do not expect Agent Memory to fetch or analyze URLs by itself. The AI agent is
responsible for reading the material, producing the extract, and deciding which
facts/decisions/preferences are durable enough to remember.

## Reviewing Pending Memory Through MCP

Use MCP review tools when the user asks to process pending memory:

1. Call `review()` to list pending agent-generated memories.
2. Call `inspect(id)` when an item needs more detail.
3. Present each item with id, type, confidence, source, summary, risk flags, and
   recommended action.
4. Call `approve(id, reason)` for durable, correct memory.
5. Call `reject(id, reason)` for incorrect, transient, duplicated, or low-value memory.

Use `mark_status(id, status)` only when you need a lifecycle state other than
`active` or `rejected`, such as `stale`.

Canonical memories should be small and atomic:

- `fact`: stable factual knowledge
- `decision`: project or architecture decision
- `preference`: user preference
- `project_context`: durable project background
- `task`: open follow-up or question
- `source_extract`: durable source summary only when the summary itself should
  be recallable as canonical memory; most raw summaries belong under `Sources/`

Example source capture:

```json
{
  "source": {
    "url": "https://example.com/article",
    "title": "Article title",
    "content": "Raw Markdown or readable text fetched by the agent.",
    "extract": "Summary, key ideas, durable facts, decisions, preferences, open questions, and relevant quotes.",
    "project": "agent-memory",
    "tags": ["source", "article"]
  },
  "memories": [
    {
      "type": "decision",
      "text": "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache.",
      "scope": "project",
      "project": "agent-memory",
      "confidence": 0.86,
      "tags": ["memory", "architecture"]
    }
  ],
  "author_name": "MCP agent"
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
