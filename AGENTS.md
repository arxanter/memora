## Memora Usage

This project uses Memora. The preferred current interface is CLI-first.

Current project direction is CLI-only for agents. Prefer `memora ... --json`
commands and generated agent instructions/skills for all memory workflows.

## Remi / Memora Policy

Treat `Remi`, `Рэми`, and `Реми` as explicit Memora triggers (override defaults with
`memora agent-aliases set …`). When the user addresses the assistant by these names,
classify the request as memory work and route it through the CLI instead of treating
it as a generic chat request.

The vault may define `.memora/config.yaml` `agent_policy` settings:

- `manual`: ask before saving or changing lifecycle status.
- `review`: create agent-authored memories as `pending`.
- `explicit_active`: explicit user saves may become `active`; inferred memories
  remain `pending`.
- `autonomous`: the assistant may write and update lifecycle status under policy, with
  source, confidence, reason, and audit history.

Use confidence consistently:

- `0.90-1.00`: explicit user instruction, direct quote, or confirmed decision.
- `0.75-0.89`: strong source-backed extraction.
- `0.55-0.74`: plausible inference that should usually stay reviewable.
- `<0.55`: ask before creating canonical memory.

## Review And Recall Policy

Do not run memora review on every turn. Check the pending review queue once at
the beginning of a new session when memory work is relevant, or when the user
asks the assistant (Remi / Рэми / Реми) to review memory.

Use `memora review --json`. If pending items exist, summarize them in a compact
review queue and ask the
   user whether to inspect, approve, reject, or defer them.
Do not approve or reject memory without explicit user confirmation unless the
vault policy is `autonomous` and the change is source-backed with a reason.

For approval, use `memora review approve <id> --reason "<reason>" --json`. For
rejection, use `memora review reject <id> --reason "<reason>" --json`.

Do not run `build_context()` for generic coding or shell tasks. For normal user
requests, first decide whether memory is relevant. Recall is relevant when the
request uses a configured assistant alias, asks about previous decisions, earlier work, stored
preferences, project history/status, or project-specific memory.

When recall is relevant, run:

`memora build-context "<task>" --project "<project-name>" --task-class planning --json`

Use returned memory only when `memory_needed` is true.

When the user asks to find information in the knowledge base, prefer CLI:

1. `memora search "<query>" --project "<project>" --json` for direct lookup and
   citations.
2. `memora recall "<query>" --budget 1200 --project "<project>" --json` when
   the agent needs compact source chunks to answer a question.
3. `memora brief "<query>" --budget 1200 --project "<project>" --json` when the
   user wants a synthesized, citation-preserving summary.

Useful filters include `project`, `type`, `status`, `scope`, `limit`,
`include_related`, and `semantic`.

## Capturing New Material

When the user asks to save a URL, article, notes, transcript, document, or raw material into memory:

1. Read or fetch the source material.
2. Create a concise extract from the material.
3. If material is unprocessed, place it in `raw/` or use
   `memora raw process ... --json` to normalize it into `Sources/...`.
   Otherwise preserve the source and extract with `memora import-source`.
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

Do not expect Memora to fetch or analyze URLs by itself. The AI agent is
responsible for reading the material, producing the extract, and deciding which
facts/decisions/preferences are durable enough to remember.

## Reviewing Pending Memory

Use CLI review commands when the user asks to process pending memory:

1. Call `memora review --json` to list pending agent-generated memories.
2. Call `memora inspect <id> --json` when an item needs more detail.
3. Present each item with id, type, confidence, source, summary, risk flags, and
   recommended action.
4. Call `memora review approve <id> --reason "<reason>" --json` for durable,
   correct memory.
5. Call `memora review reject <id> --reason "<reason>" --json` for incorrect,
   transient, duplicated, or low-value memory.

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
    "project": "memora",
    "tags": ["source", "article"]
  },
  "memories": [
    {
      "type": "decision",
      "text": "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache.",
      "scope": "project",
      "project": "memora",
      "confidence": 0.86,
      "tags": ["memory", "architecture"]
    }
  ],
  "author_name": "CLI agent"
}
```

Example durable memory after extraction:

```json
{
  "type": "decision",
  "text": "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache.",
  "scope": "project",
  "project": "memora",
  "confidence": 0.86,
  "source": {
    "url": "https://example.com/article",
    "title": "Article title"
  },
  "tags": ["memory", "architecture"]
}
```
