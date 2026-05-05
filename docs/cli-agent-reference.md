# Memora CLI Agent Reference

Purpose: compact command map for agents. Commands default to compact
agent-readable text. Normal installs resolve storage from `MEMORA_HOME`.

Common options:

- `--vault PATH`, `-v PATH`: legacy/advanced override for non-standard vaults.
- `--project NAME`: project filter/metadata where supported.
- `--dry-run`: validate or preview without writing where supported.

Value sets:

- Clients: `all`, `agents`, `cursor`, `claude`, `codex`.
- Memory types: `fact`, `decision`, `preference`, `task`,
  `project_context`, `conversation_summary`.
- Statuses: `active`, `pending`, `stale`, `rejected`, `superseded`.
- Recall task classes: `default`, `coding`, `planning`, `review`.
- Search modes: `auto`, `text`, `vector`, `hybrid`.
- Raw/source kinds: `pdf`, `zoom`, `slack`, `text`.
- Raw/source formats: `pdf`, `markdown`, `json`, `txt`.

## Agent Routing

Use these most often:

- `memora probe "<query>" --project <project> --variant "<alternate wording>"`
- `memora build-context "<task>" --project <project> --task-class planning`
- `memora search "<query>" --project <project>`
- `memora inspect <id>`
- `memora remember --type <type> --text "<atomic memory>" --project <project>`
- `memora memory update <id> --scope user --clear-project`
- `memora raw add <path> --kind <kind> --format <format>`
- `memora source add <source.md> --extract <extract.md> --kind <kind>`
- `memora raw mark-processed <raw-path> --source-id <source_id>`
- `memora wiki ingest <source_id> --entity <name> --concept <name>`
- `memora context "<query>" --project <project> --intent auto --budget 1200`
- `memora review`
- `memora review approve <id...> --reason "<reason>"`
- `memora review reject <id...> --reason "<reason>"`

Do not edit vault files directly. If the needed operation is not listed, stop
and report the CLI gap.

## Home And Health

`memora setup [home] [--dry-run]`

- Preview or create the managed Memora home. Without `<home>`, uses
  `MEMORA_HOME` before falling back to `~/memora`.
- The managed layout is `engine/`, `vault/`, `config.yaml`, `state/`, and
  `venv/`.

`memora self update [--checkout PATH] [--remote NAME] [--remote-url URL] [--branch NAME] [--wrapper PATH] [--reinstall|--no-reinstall] [--dry-run]`

- Soft-update the managed `engine/` checkout with stash/pull/pop.
- By default, rerun the installer afterward to refresh `venv/`, wrapper, runtime
  dependencies, and the default local semantic provider. This never removes or
  rewrites `vault/`.

`memora help`

- Return public command groups. Useful for lightweight capability discovery.

`memora status [--vault PATH]`

- Summarize vault health and index state.

`memora doctor [--vault PATH]`

- Validate memory Markdown schema and graph links.

`memora reindex [--vault PATH] [--clean]`

- Rebuild local SQLite index from Markdown.

## Agent Setup

`memora agent rules [--client <client>] [--scope project|user] [--vault PATH] [--project NAME] [--alias NAME ...]`

- Generate Memora instructions for an agent client.

`memora agent integrate [--client <client>] [--scope project|user] [--project PATH] [--target PATH] [--vault PATH] [--alias NAME ...] [--dry-run] [--force]`

- Install generated instructions into a project/user target. For project scope,
  run from the target project or pass `--project PATH`; Memora refuses implicit
  writes from its own source checkout.
- User scope writes Claude to `~/.claude/CLAUDE.md` and Codex to
  `~/.codex/AGENTS.md`; Cursor user scope emits a fallback file under
  `~/.memora/` for manual installation because Cursor user rules are
  settings-managed.

`memora agent update [--client <client>] [--scope project|user] [--project PATH] [--target PATH] [--vault PATH] [--alias NAME ...] [--dry-run] [--force]`

- Update managed instruction blocks.

`memora agent status [--client <client>] [--scope project|user] [--project PATH] [--vault PATH] [--alias NAME ...]`

- Check installed instruction status.

`memora agent-aliases list [--vault PATH]`

- Show Remi-style assistant aliases.

`memora agent-aliases set <name...> [--vault PATH]`

- Persist assistant aliases.

## Capture And Sources

`memora raw add <path> --kind <kind> --format <format> [--title TEXT] [--sensitivity normal|private|secret|unsafe] [--tag TAG ...] [--dry-run] [--vault PATH]`

- Copy raw input into staging with sidecar metadata. Does not create memories.

`memora raw list [path] [--vault PATH]`

- List staged raw inbox files. Optional `path` defaults to vault `raw/inbox`.

`memora raw inspect <path> [--vault PATH]`

- Inspect raw metadata and preview text when available.

`memora raw mark-processed <path> [--source-id <source_id>] [--dry-run] [--vault PATH]`

- Move a successfully processed raw file and sidecar metadata to `raw/processed`.
  Run this after curated source evidence has been saved with `memora source add`.

`memora source add <source.md> [--extract <extract.md>] [--kind <kind>] [--format <format>] [--title TEXT] [--url URL] [--sensitivity normal|private|secret|unsafe] [--tag TAG ...] [--vault PATH]`

- Save curated durable evidence under `Sources/`.

`memora lookup-source <source_id> [--query TEXT] [--budget N] [--session-id ID] [--loaded-source-id ID ...] [--vault PATH]`

- Return compact source evidence with citations.

## Wiki

`memora wiki status [--vault PATH]`

- Show Wiki page counts, recent log entries, and lint issue count.

`memora wiki read <path-or-id> [--full] [--max-chars N] [--vault PATH]`

- Read one Wiki page. Default output is a compact preview; use `--full` only
  when the page body is explicitly needed.

`memora wiki search <query> [--limit N] [--vault PATH]`

- Search only `Wiki/` pages and return compact candidates plus read commands.

`memora wiki ingest <source_id> [--title TEXT] [--entity NAME ...] [--concept NAME ...] [--vault PATH]`

- Create or update `Wiki/sources/<source>.md`, ensure related entity/concept
  pages, update `Wiki/index.md`, and append `Wiki/log.md`.

`memora wiki synthesize <question> [--title TEXT] [--save] [--limit N] [--vault PATH]`

- Draft a synthesis page from Wiki and memory candidates. Without `--save`, it
  prints a candidate only. Durable saved briefs, analyses, and query answers
  belong in `Wiki/syntheses/`.

`memora wiki lint [--vault PATH]`

- Check Wiki broken links, missing citations, and orphan pages.

## Memory Writes And Review

`memora remember --type <memory_type> --text TEXT [--scope user|project] [--project NAME] [--status <status>] [--tag TAG ...] [--vault PATH]`

- Create one canonical atomic memory.
- Legacy `source_extract` memories remain readable, but new writes should use
  `memora source add` for evidence and `memora wiki synthesize --save` for
  durable source-backed summaries.

`memora memory update <id> [--type <memory_type>] [--scope user|project|global] [--project NAME|--clear-project] [--status <status>] [--confidence N|--clear-confidence] [--tag TAG ...|--clear-tags] [--title TEXT|--clear-title] [--text TEXT] [--reason TEXT] [--dry-run] [--vault PATH]`

- Update safe editable fields on an existing canonical memory.
- `--tag` replaces the full tag list; repeat it for multiple tags. Use
  `--clear-tags` to remove all tags.
- Changing `--type` moves the Markdown file into the corresponding
  `Memories/` type directory.
- Non-project scopes clear `project` automatically unless `--project` is
  supplied.

`memora review [--group-by source] [--vault PATH]`

- List pending agent-authored memories.

`memora review approve <id...> [--reason TEXT] [--dry-run] [--override-unsafe] [--vault PATH]`

- Approve pending memories.

`memora review reject <id...> [--reason TEXT] [--dry-run] [--vault PATH]`

- Reject pending memories.

## Retrieval And Context

`memora probe <query> [--variant TEXT ...] [--project NAME] [--intent auto|memory|wiki|evidence|mixed] [--task-class <class>] [--budget N] [--limit N] [--load] [--semantic|--no-semantic] [--mode <mode>] [--refresh|--no-refresh] [--vault PATH]`

- Single-call agent probe for "is there anything relevant in memory?" checks.
- Searches only `Memories/` and `Wiki/`; use `context --intent evidence` or
  `lookup-source` when saved source evidence is required.
- Agents should pass likely alternate constructions with repeated `--variant`
  instead of issuing separate `build-context`, `context`, and `search` calls.
  Include concise synonyms, RU/EN translations, important inflections/cases,
  abbreviations, and domain terms.
- Agents should pass an explicit `--intent memory`, `--intent wiki`, or
  `--intent mixed` when they can confidently classify the request. Use
  `--intent auto` as the fallback when unsure.
- Returns `has_context`, `memory_needed`, route, checked variants, semantic
  status, compact candidates, and expansion commands.
- For `probe`, `has_context=true` means at least one selected surface returned
  candidates. `memory_needed=true` is narrower and means the memory surface
  returned candidates.

`memora context <query> [--budget N] [--project NAME] [--intent auto|memory|wiki|evidence|mixed] [--limit N] [--load] [--refresh|--no-refresh] [--vault PATH]`

- Route a query across `Memories/`, `Wiki/`, and `Sources/` with a strict
  compact output contract.
- Default output returns the route decision, per-surface budgets, compact
  candidates, and expansion commands. Use `--load` sparingly when snippets are
  needed immediately.

`memora build-context <task> [--budget N] [--project NAME] [--task-class <class>] [--include-related] [--include-profile|--no-include-profile] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--loaded-source-id ID ...] [--refresh|--no-refresh] [--vault PATH]`

- Main agent recall command. Use returned context only when
  `memory_needed=true`.
- Prefer `probe` for initial discovery with query variants; use `build-context`
  when a packed cited brief is needed after discovery.
- If trigger policy does not request memory, `build-context` probes indexed
  keyword and local semantic results before returning `memory_needed=false`.
- `--include-profile` adds a bounded in-memory rollup to this response.
- Default output is compact agent text.

`memora search <query> [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--created-after DATE] [--created-before DATE] [--updated-after DATE] [--updated-before DATE] [--valid-from DATE] [--valid-to DATE] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--refresh|--no-refresh] [--limit N] [--vault PATH]`

- Ranked search with snippets and citations.
- Default output is a compact candidate list with IDs. Use `memora inspect <id>`
  to load a full memory only when needed.

`memora recall <query> [--budget N] [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--task-class <class>] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--loaded-source-id ID ...] [--refresh|--no-refresh] [--vault PATH]`

- Pack memory chunks under a token budget.

`memora brief <query> [--budget N] [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--task-class <class>] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--refresh|--no-refresh] [--vault PATH]`

- Produce citation-preserving Markdown context.
- Default output is a compact cited brief with memory IDs.
- Brief output is ephemeral stdout/JSON only; save durable analyses as
  `Wiki/syntheses/`.

## Inspect And Open

`memora inspect <id> [--vault PATH]`

- Show one memory by id.
- Default output shows metadata and body without absolute vault/debug fields.

`memora open <id> [--launch] [--vault PATH]`

- Print memory path; optionally open the Markdown file.

## Session Capture

`memora session finalize [transcript] [--transcript PATH] --summary-file <summary.md> [--memories-file <memories.json>] [--format TEXT] [--project NAME] [--tag TAG ...] [--sensitivity normal|private|secret|unsafe] [--confidence 0..1] [--dry-run] [--vault PATH]`

- Save session transcript/source, summary extract, and optional proposed
  memories for review.

