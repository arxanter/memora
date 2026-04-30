# MCP Integrations

The MCP server is the primary coding-agent interface. It uses the same config
loader, schema validation, Markdown write path, retrieval, recall, brief, and
lifecycle services as the CLI.

## Install

Install the project with the optional MCP dependency:

```bash
pip install -e ".[mcp]"
```

Initialize a vault if needed:

```bash
memory init ~/MemoryVault --json
```

Point agent clients at the vault with `AGENT_MEMORY_VAULT`:

```bash
export AGENT_MEMORY_VAULT=~/MemoryVault
```

## Tool Policy

Available tools:

- `remember(memory)`
- `save_source(source)`
- `ingest_url(url, title, content, extract, project, tags)`
- `search(query, filters)`
- `recall(query, budget, filters)`
- `brief(query, budget, filters)`
- `should_recall(message)`
- `build_context(task, budget, filters)`
- `inspect(id)`
- `explain_recall(query, budget, filters)`
- `review()`
- `approve(id, reason)`
- `reject(id, reason)`
- `mark_status(id, status)`
- `mark_superseded(old_id, by_id, reason)`

Agent-authored memories default to the config's `agent_default_status`, which is
`pending` in the generated config. They include `author.kind: agent` and require
source and confidence metadata through the shared schema validator. The server
supplies safe defaults when an agent omits source or confidence.

`save_source(source)` and `ingest_url(...)` save raw material under `Sources/`.
They do not perform LLM analysis and do not promote content into canonical memory
by themselves. The agent should fetch/read/analyze material, call one of these
tools to preserve the source and extract, then call `remember(memory)` for
durable atomic facts, decisions, preferences, project context, or tasks.

`should_recall(message)` is a deterministic policy check with no LLM dependency.
It returns `should_recall`, `confidence`, and matched `triggers`. Agents should
call it before spending context on memory for a user request.

`build_context(task, budget, filters)` is the recommended automatic recall entry
point. It runs `should_recall` first; if memory is useful, it returns the same
citation-preserving Memory Brief payload available from `brief`. If memory is
not useful, it returns `memory_needed: false`, empty Markdown, and no citations
without requiring a vault or index.

`mark_status` and `mark_superseded` mutate lifecycle frontmatter through the
Stage 9 lifecycle service. `explain_recall` returns deterministic selected and
skipped recall explanations backed by retrieval and packing metadata.

`review()` returns pending agent-generated memories. `approve(id, reason)` marks
a pending memory `active`; `reject(id, reason)` marks it `rejected`. These are
the MCP equivalents of `memory review`, `memory mark <id> --status active`, and
`memory reject <id>`.

## Automatic Recall Policy

Recall is recommended when a request asks what was previously decided,
references earlier work, asks about user preferences, asks project-specific
questions about this repo/codebase/workspace, or asks for history/status.

Representative recall requests:

```text
What did we decide about embeddings?
Use the same approach as in the previous implementation.
What are my testing preferences for this repo?
In this codebase, how do we handle lifecycle status?
Where did we leave off on Stage 9?
```

Representative no-recall requests:

```text
Write a Python function that reverses a list.
Run git status.
Explain what a binary search tree is.
Create a new React project called dashboard.
```

## Source Capture Workflow

When the user asks to save a URL, article, notes, transcript, document, or raw
material into memory, agents should follow this workflow:

1. Read or fetch the source material using the agent's normal browser/file tools.
2. Call `save_source` or `ingest_url` with the raw content and a structured
   extract.
3. Promote only durable atomic items with `remember(memory)`.
4. Leave agent-created memories `pending` for review.
5. Tell the user which source paths and pending memories were created.

Suggested extract shape:

```markdown
## Summary

## Key Ideas

## Durable Facts

## Decisions

## Preferences

## Open Questions

## Relevant Quotes
```

Example `ingest_url` call:

```json
{
  "url": "https://example.com/article",
  "title": "Article title",
  "content": "Raw Markdown or readable text fetched by the agent.",
  "extract": "## Summary\n...\n\n## Durable Facts\n- ...",
  "project": "agent-memory",
  "tags": ["source", "article"]
}
```

Then call `remember(memory)` for each durable item:

```json
{
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
```

## Codex

Add an MCP server entry to the Codex MCP configuration and pass the vault path as
an environment variable:

```toml
[mcp_servers.agent-memory]
command = "memory-mcp"
env = { AGENT_MEMORY_VAULT = "/Users/you/MemoryVault" }
```

If `memory-mcp` is not on `PATH`, use the Python module form:

```toml
[mcp_servers.agent-memory]
command = "python"
args = ["-m", "agent_memory.mcp_server"]
env = { AGENT_MEMORY_VAULT = "/Users/you/MemoryVault" }
```

Recommended Codex workflow:

1. Call `build_context(task, budget, filters)` for each substantial user task.
2. If `memory_needed` is `false`, answer normally.
3. If `memory_needed` is `true`, read `markdown` and honor the returned
   citations.
4. Use `remember(memory)` only for durable facts, preferences, decisions, or
   project context that should enter the review queue.
5. Use `mark_superseded(old_id, by_id, reason)` when replacing an older memory
   with a newer durable decision.

## Claude Code

Register the server as a stdio MCP process:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

Claude Code can then call `remember` with a memory object such as:

```json
{
  "type": "decision",
  "text": "Use Markdown as the durable memory source.",
  "scope": "project",
  "project": "agent-memory",
  "confidence": 0.82,
  "source": {
    "path": "Sources/2026-04-30_mcp/source.md",
    "title": "MCP setup notes"
  },
  "tags": ["mcp", "memory"]
}
```

Recommended Claude Code workflow:

1. Start with `build_context(task, budget, filters)` instead of calling `brief`
   directly.
2. If `memory_needed` is `false`, do not spend context on memory.
3. If `memory_needed` is `true`, prepend the returned `markdown` to the working
   context and preserve citations in summaries.
4. Use `remember(memory)` for new durable memory; it defaults to pending review.
5. Use `review()`, `approve(id, reason)`, or `reject(id, reason)` to process the
   pending review queue through MCP.
6. Use `mark_status(id, status)` or `mark_superseded(old_id, by_id, reason)` for
   explicit lifecycle updates.
7. For "save this URL/material" requests, fetch the material, call `ingest_url`
   or `save_source`, then promote durable items with `remember`.

## Cursor

Add an MCP server entry in Cursor settings:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

Restart or reload Cursor after changing MCP settings so the server process is
discovered.

Recommended Cursor workflow:

1. Call `build_context` before codebase work that references previous decisions,
   earlier work, preferences, project-specific behavior, or project status.
2. Skip memory when `memory_needed` is `false`.
3. Use `remember` only when the user explicitly asks to save a durable memory or
   when a completed task creates a stable decision worth review.
4. Use `review`, `approve`, and `reject` to process pending memory without
   leaving the MCP workflow.
5. For URLs and raw material, save source/extract first with `ingest_url` or
   `save_source`, then create pending atomic memories with `remember`.

