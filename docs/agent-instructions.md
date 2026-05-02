# Agent Instructions For Memory Workflows

Use this document when configuring Claude Code, Cursor, Codex, or another coding
agent to work with Agent Memory. Copy the relevant sections into project-level
`AGENTS.md`, `CLAUDE.md`, or `.cursor/rules/agent-memory.mdc`.

You can generate focused project rules instead of copying this file manually:

```bash
memory agent-rules --format agents
memory agent-rules --format cursor
memory agent-rules --format claude
memory agent-rules --format codex
memory install-agent-rules --client cursor --project <path> --dry-run
```

Current product direction is CLI-first. Prefer `memory ... --json` commands from
any project directory. MCP is paused/outdated for now and should be treated only
as a legacy optional adapter unless the user explicitly reopens that decision.
When using that legacy adapter, `save_source`, `ingest_url`, and `remember`
correspond to the CLI source-preservation and atomic-memory workflows below.

## Core Rule

Agent Memory stores and retrieves durable context. The AI agent does the
understanding work.

```text
AI agent:
  read/fetch material
  summarize and extract durable information
  call memory raw/import/source commands to preserve material
  call memory remember/review lifecycle commands for atomic durable memories

Agent Memory:
  validate and store Markdown
  index and retrieve memories
  pack context under budget
  preserve citations and lifecycle state
```

Default capture starts in `raw/` when material is unprocessed, then normalizes
into `Sources/`; canonical `Memories/` should receive only separate atomic
promotions.

## Startup Recall

Do not spend memory context on every user message. Recall is recommended when the
request addresses `Toby`, `Тоби`, or `tb`; asks what was previously decided;
references earlier work; asks about preferences; asks project-specific questions;
or asks for history/status.

Review the pending queue once near session startup when memory work is relevant,
or when the user explicitly asks Toby to review memory:

```text
review()
```

If the current MCP client does not expose `review`, use the CLI fallback:

```bash
memory review --json
```

When pending items exist, summarize them with id, type, confidence, source,
summary, risk flags, and recommended action. Ask whether to inspect, approve,
reject, or defer each item. Do not approve or reject memory without explicit user
confirmation unless the vault policy is `autonomous` and the lifecycle change is
source-backed with an audit reason.

When recall is relevant, call:

```bash
memory build-context "<task>" --project "<project-name>" --task-class planning --json
```

Use returned memory only when `memory_needed` is true. Preserve citations when
summarizing or making decisions from recalled memory.

## Toby Triggers And Trust Levels

Treat `Toby`, `Тоби`, and `tb` as explicit Agent Memory aliases.

Intent routing:

- `Toby, что мы решили ...`: run `memory build-context` or `memory brief` and answer with citations.
- `Toby, сохрани ...`: save memory according to `agent_policy.trust_level`.
- `Toby, проанализируй статью и сохрани ...`: fetch/read the source, create an extract, save source/extract, then promote durable atomic memories.
- `Toby, review memory`: run `memory review --json` and present a readable queue.
- `Toby, актуализируй память`: find related entries and propose or apply lifecycle changes according to policy.

Recommended `.agent-memory/config.yaml` policy shape:

```yaml
agent_policy:
  aliases: [Toby, Тоби, tb]
  trust_level: review
  default_recall_budget: 1200
  min_active_confidence: 0.85
  min_pending_confidence: 0.55
  explicit_user_saves_active: true
  autonomous_lifecycle: false
  require_review_for_source_extracts: true
```

Trust levels:

- `manual`: ask before saving or changing lifecycle status.
- `review`: create agent-authored memories as `pending`.
- `explicit_active`: explicit user saves may become `active`; inferred memories remain `pending`.
- `autonomous`: Toby may create memories and change lifecycle status under policy, with source, confidence, reason, and audit history.

Confidence guidance:

- `0.90-1.00`: explicit user instruction, direct quote, or confirmed project decision.
- `0.75-0.89`: strong source-backed extraction or clear document fact.
- `0.55-0.74`: reasonable inference from source; keep reviewable by default.
- `<0.55`: do not create canonical memory without asking; keep as source/extract or open question.

Ask the user before saving or mutating memory when scope/project is ambiguous,
content may contain secrets, a new item contradicts active memory, confidence is
below the configured threshold, or the user asks only to analyze/propose.

## Capturing URLs And Raw Material

When the user asks to save a URL, article, notes, transcript, document, or raw
material into memory:

1. Fetch or read the material with the agent's normal browser/file tools.
2. Produce a concise extract.
3. If the material is unprocessed, place it in `raw/` or run
   `memory raw process ... --json`; otherwise run `memory import-source ...`.
4. Call `memory remember` for each durable atomic memory extracted from the source. Do
   not duplicate the saved `Sources/.../extract.md` summary as a default
   canonical `source_extract`.
5. Apply `agent_policy`: inferred agent-created memories remain `pending`;
   explicit user saves may become `active` only when the configured trust level
   and confidence threshold allow it.

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

Agent-created memories should stay `pending` until reviewed unless
`agent_policy.trust_level` allows direct activation for an explicit user save:

```bash
memory review
memory mark <id> --status active
memory reject <id>
memory reindex
```

Do not store secrets, temporary logs, one-off debugging traces, or unreviewed raw
dumps as canonical memory.

## Finding Information

Use natural-language questions rather than trying to remember exact filenames.
The usual choices are:

```bash
memory search "<query>" --project "<project>" --json
memory recall "<query>" --project "<project>" --budget 1200 --json
memory brief "<query>" --project "<project>" --budget 1200 --json
```

Use `search` for direct lookup, `recall` for compact cited context, and `brief`
for a synthesized answer. Useful filters include `project`, `type`, `status`,
`scope`, `limit`, `include_related`, and `semantic`.
