# Agent Instructions For Memory Workflows

Use this document when configuring Claude Code, Cursor, Codex, or another coding
agent to work with Memora. Copy the relevant sections into project-level
`AGENTS.md`, `CLAUDE.md`, or `.cursor/rules/memora.mdc`.

You can generate focused project rules instead of copying this file manually:

```bash
memora agent-rules --format agents
memora agent-rules --format cursor
memora agent-rules --format claude
memora agent-rules --format codex
memora install-agent-rules --client cursor --project <path> --dry-run
memora install-agent-rules --client codex --project <path> --dry-run
memora agent-install-commands --client all
```

Current product direction is CLI-first and CLI-only for agents. Use only
`memora ... --json` commands from any project directory for recall, search,
source lookup, imports, writes, review, lifecycle, status, indexing, and session
capture.

Do not read, write, edit, delete, or migrate Memora vault files directly.
This includes `Memories/`, `Sources/`, `Briefs/`, `Profiles/`, `Synthesis/`,
`raw/`, `.memora/index.sqlite`, cache, embeddings, locks, and schema
files. Treat vault paths, SQLite/cache internals, frontmatter, filenames, and
generated schema as private storage managed by the CLI.

If the CLI lacks an operation, stop and report the missing command or product
gap. Do not bypass the CLI with direct file edits, SQL, migrations, cache
manipulation, or ad hoc scripts.

## Core Rule

Memora stores and retrieves durable context. The AI agent does the
understanding work.

```text
AI agent:
  read/fetch material through normal tools
  summarize and extract durable information
  call memora raw/import/source commands with --json to preserve material
  call memora remember/review lifecycle commands with --json

Memora:
  validate and store Markdown
  index and retrieve memories
  pack context under budget
  preserve citations and lifecycle state
```

Default capture starts in `raw/` when material is unprocessed, then normalizes
through CLI commands into `Sources/`; canonical `Memories/` should receive only
separate atomic promotions. Agents may cite returned paths, but paths are not an
invitation to mutate files directly.

## Startup Recall

Do not spend memory context on every user message. Recall is recommended when
the request addresses `Remi`, `Рэми`, or `Реми` (or names from `memora agent-aliases list`); asks for current facts,
decisions, preferences, earlier work, project history/status; or asks to save or
analyze durable knowledge.

Review the pending queue once near session startup when memory work is relevant,
or when the user explicitly asks the assistant to review memory:

```bash
memora review --json
```

When pending items exist, summarize them with id, type, confidence, source,
summary, risk flags, and recommended action. Ask whether to inspect, approve,
reject, or defer each item. Do not approve or reject memory without explicit user
confirmation unless the vault policy is `autonomous` and the lifecycle change is
source-backed with an audit reason.

When recall is relevant, call:

```bash
memora build-context "<task>" --project "<project-name>" --task-class planning --json
```

Use returned memory only when `memory_needed` is true. Preserve citations when
summarizing or making decisions from recalled memory.

## Assistant routing (Remi)

Treat `Remi`, `Рэми`, and `Реми` as explicit Memora aliases unless the vault overrides them (`agent_policy.aliases`; change with `memora agent-aliases set …`).

Intent routing:

- `Remi, show current facts about <topic>` / `Рэми, покажи текущие факты по <topic>` / `Реми, покажи текущие факты по <topic>`: run `memora brief` or `memora search`, then answer with citations.
- `Remi, what did we decide about <topic>` / `Рэми, что мы решили по <topic>` / `Реми, что мы решили по <topic>`: run `memora build-context`; use returned memory only if `memory_needed=true`.
- `Remi, save this fact/decision/preference` / `Рэми, сохрани это как факт/решение/preference` / `Реми, сохрани это как факт/решение/preference`: create one atomic memory with `memora remember --json`; lifecycle follows `agent_policy`.
- `Remi, review pending memory` / `Рэми, проверь pending memory` / `Реми, проверь pending memory`: run `memora review --json`, present a compact queue, and ask before approve/reject unless policy allows autonomous action.
- `Remi, update memory for <topic>` / `Рэми, актуализируй память по <topic>` / `Реми, актуализируй память по <topic>`: search related active/pending items, propose supersede/reject/defer/new memory, and ask before lifecycle changes unless policy allows autonomous action.
- `Remi, analyze this source and save it` / `Рэми, проанализируй источник и сохрани` / `Реми, проанализируй источник и сохрани`: read/fetch the source, create an extract, preserve the source, then promote only durable atomic items.

Useful commands:

```bash
memora brief "<topic>" --project "<project>" --json
memora search "<query>" --project "<project>" --json
memora remember --type decision --text "<durable decision>" --project "<project>" --json
```

## Trust Levels

Recommended `.memora/config.yaml` policy shape:

```yaml
agent_policy:
  aliases: [Remi, Рэми, Реми]
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
- `autonomous`: the assistant may create memories and change lifecycle status under policy, with source, confidence, reason, and audit history.

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
3. Preserve unprocessed material with `memora raw process ... --json`; otherwise
   run `memora import-source ... --json` or a connector-specific import command.
4. Call `memora remember --json` only for durable atomic memory extracted from
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
memora import-source ./article.md \
  --extract-file ./article-extract.md \
  --project memora \
  --tag article \
  --json
```

Use explicit connector commands only when the user asks for that source:

```bash
memora import-url https://example.com/article --dry-run --json
memora import-pdf ./paper.pdf --text-file ./paper.txt --project memora --json
memora import-zoom ./meeting-summary.md --project memora --json
memora import-slack ./thread.json --channel "#memora" --json
memora source-inbox scan --path ./raw/inbox --ignore-disabled --dry-run --json
```

Promote a durable atomic decision after preserving the source:

```bash
memora remember \
  --type decision \
  --scope project \
  --project memora \
  --text "Use Obsidian Markdown as durable memory; SQLite remains rebuildable cache." \
  --json
```

## Review Policy

Agent-created memories should stay `pending` until reviewed unless
`agent_policy.trust_level` allows direct activation for an explicit user save:

```bash
memora review --json
memora review approve <id> --reason "verified source" --json
memora review reject <id> --reason "not durable" --json
memora review defer <id> --reason "needs later review" --json
memora reindex --json
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
memora import-session ./session.jsonl \
  --summary-file ./session-summary.md \
  --remember-summary \
  --project memora \
  --json
```

Do not turn routine implementation chatter into canonical memories. Capture only
information that is likely to be useful in a future session.

## Chat Noise

Do not narrate every `memora ... --json` call or paste large JSON into chat
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
memora search "<query>" --project "<project>" --json
memora recall "<query>" --project "<project>" --budget 1200 --json
memora brief "<query>" --project "<project>" --budget 1200 --json
```

Use `search` for direct lookup, `recall` for compact cited context, and `brief`
for a synthesized answer. Useful filters include `project`, `type`, `status`,
`scope`, `limit`, `include_related`, and `semantic`.
