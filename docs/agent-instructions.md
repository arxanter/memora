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
memory install-agent-rules --client codex --project <path> --dry-run
memory agent-install-commands --client all
```

Current product direction is CLI-first and CLI-only for agents. Use only
`memory ... --json` commands from any project directory for recall, search,
source lookup, imports, writes, review, lifecycle, status, indexing, and session
capture.

Do not read, write, edit, delete, or migrate Agent Memory vault files directly.
This includes `Memories/`, `Sources/`, `Briefs/`, `Profiles/`, `Synthesis/`,
`raw/`, `.agent-memory/index.sqlite`, cache, embeddings, locks, and schema
files. Treat vault paths, SQLite/cache internals, frontmatter, filenames, and
generated schema as private storage managed by the CLI.

If the CLI lacks an operation, stop and report the missing command or product
gap. Do not bypass the CLI with direct file edits, SQL, migrations, cache
manipulation, or ad hoc scripts.

## Core Rule

Agent Memory stores and retrieves durable context. The AI agent does the
understanding work.

```text
AI agent:
  read/fetch material through normal tools
  summarize and extract durable information
  call memory raw/import/source commands with --json to preserve material
  call memory remember/review lifecycle commands with --json

Agent Memory:
  validate and store Markdown
  index and retrieve memories
  pack context under budget
  preserve citations and lifecycle state
```

Default capture starts in `raw/` when material is unprocessed, then normalizes
through CLI commands into `Sources/`; canonical `Memories/` should receive only
separate atomic promotions. Agents may cite returned paths, but paths are not an
invitation to mutate files directly.

If a legacy MCP client is the only available interface, use `save_source`,
`ingest_url`, and `remember` as compatibility equivalents for the CLI source
preservation and atomic-memory workflows. Do not expand MCP-specific behavior
unless the project direction explicitly reopens it.

## Startup Recall

Do not spend memory context on every user message. Recall is recommended when
the request addresses `Toby`, `Тоби`, or `tb`; asks for current facts,
decisions, preferences, earlier work, project history/status; or asks to save or
analyze durable knowledge.

Review the pending queue once near session startup when memory work is relevant,
or when the user explicitly asks Toby to review memory:

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

## Toby Routing

Treat `Toby`, `Тоби`, and `tb` as explicit Agent Memory aliases.

Intent routing:

- `Toby, show current facts about <topic>` / `Тоби, покажи текущие факты по <topic>`: run `memory brief` or `memory search`, then answer with citations.
- `Toby, what did we decide about <topic>` / `Тоби, что мы решили по <topic>`: run `memory build-context`; use returned memory only if `memory_needed=true`.
- `Toby, save this fact/decision/preference` / `Тоби, сохрани это как факт/решение/preference`: create one atomic memory with `memory remember --json`; lifecycle follows `agent_policy`.
- `Toby, review pending memory` / `Тоби, проверь pending memory`: run `memory review --json`, present a compact queue, and ask before approve/reject unless policy allows autonomous action.
- `Toby, update memory for <topic>` / `Тоби, актуализируй память по <topic>`: search related active/pending items, propose supersede/reject/defer/new memory, and ask before lifecycle changes unless policy allows autonomous action.
- `Toby, analyze this source and save it` / `Тоби, проанализируй источник и сохрани`: read/fetch the source, create an extract, preserve the source, then promote only durable atomic items.

Useful commands:

```bash
memory brief "<topic>" --project "<project>" --json
memory search "<query>" --project "<project>" --json
memory remember --type decision --text "<durable decision>" --project "<project>" --json
```

## Trust Levels

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

## Source Capture Workflow

When the user asks to save a URL, article, notes, transcript, document, or raw
material into memory:

1. Fetch or read the material with the agent's normal browser/file tools.
2. Produce a concise extract.
3. Preserve unprocessed material with `memory raw process ... --json`; otherwise
   run `memory import-source ... --json` or a connector-specific import command.
4. Call `memory remember --json` only for durable atomic memory extracted from
   the source. Do not duplicate the saved `Sources/.../extract.md` summary as a
   default canonical `source_extract`.
5. Apply `agent_policy`: inferred agent-created memories remain `pending`;
   explicit user saves may become `active` only when the configured trust level
   and confidence threshold allow it.

Do not store secrets, raw dumps, temporary logs, or unreviewed summaries as
canonical memory. Raw material belongs in `Sources/`; canonical memory belongs in
`Memories/` and should be small, durable, cited when possible, and reviewable.

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

## CLI Capture Examples

Save a source after reading it and writing a concise extract:

```bash
memory import-source ./article.md \
  --extract-file ./article-extract.md \
  --project agent-memory \
  --tag article \
  --json
```

Use explicit connector commands only when the user asks for that source:

```bash
memory import-url https://example.com/article --dry-run --json
memory import-pdf ./paper.pdf --text-file ./paper.txt --project agent-memory --json
memory import-zoom ./meeting-summary.md --project agent-memory --json
memory import-slack ./thread.json --channel "#agent-memory" --json
memory source-inbox scan --path ./raw/inbox --ignore-disabled --dry-run --json
```

Promote a durable atomic decision after preserving the source:

```bash
memory remember \
  --type decision \
  --scope project \
  --project agent-memory \
  --text "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache." \
  --json
```

## Review Policy

Agent-created memories should stay `pending` until reviewed unless
`agent_policy.trust_level` allows direct activation for an explicit user save:

```bash
memory review --json
memory review approve <id> --reason "verified source" --json
memory review reject <id> --reason "not durable" --json
memory review defer <id> --reason "needs later review" --json
memory reindex --json
```

Present review items with id, type, confidence, source, risk flags, summary, and
recommended action. Do not approve, reject, defer, supersede, or mark active
without explicit confirmation unless the vault policy allows autonomous lifecycle
changes with source, confidence, reason, and audit history.

## Session-End Capture

At the end of a substantial task, produce one concise summary of decisions,
durable facts, tasks, and open questions. If a transcript/export is available,
import it through the CLI and create pending summary memory when useful:

```bash
memory import-session ./session.jsonl \
  --summary-file ./session-summary.md \
  --remember-summary \
  --project agent-memory \
  --json
```

Do not turn routine implementation chatter into canonical memories. Capture only
information that is likely to be useful in a future session.

## Chat Noise

Do not narrate every `memory ... --json` call or paste large JSON into chat
unless the user asks. Summarize final effects only: source saved, pending
memories created, review required, no durable memory found, or CLI gap
encountered.

## Scheduled Tasks

When the user asks for a scheduled memory task, confirm source boundaries if
ambiguous; fetch only requested sources; never persist secrets, credentials, auth
tokens, private personal data, or raw mailbox dumps as canonical memory; create
one extract per run; promote only durable atomic facts, decisions, preferences,
or tasks; and return source count, pending memory count, and the review command.

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
