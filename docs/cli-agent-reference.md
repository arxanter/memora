# Memora CLI Agent Reference

Purpose: compact command map for agents. Prefer `--json` for machine-readable
output. Omit `--vault` unless the default vault is not configured.

Common options:

- `--json`: structured output for agents.
- `--vault PATH`, `-v PATH`: override vault resolution.
- `--project NAME`: project filter/metadata where supported.
- `--dry-run`: validate or preview without writing where supported.

Value sets:

- Clients: `all`, `agents`, `cursor`, `claude`, `codex`.
- Memory types: `fact`, `decision`, `preference`, `task`,
  `project_context`, `source_extract`, `conversation_summary`.
- Statuses: `active`, `pending`, `stale`, `rejected`, `superseded`.
- Recall task classes: `default`, `coding`, `planning`, `review`.
- Search modes: `auto`, `text`, `vector`, `hybrid`.
- Raw/source kinds: `pdf`, `zoom`, `slack`, `text`.
- Raw/source formats: `pdf`, `markdown`, `json`, `txt`.

## Agent Routing

Use these most often:

- `memora build-context "<task>" --project <project> --task-class planning --json`
- `memora remember --type <type> --text "<atomic memory>" --project <project> --json`
- `memora raw add <path> --kind <kind> --format <format> --project <project> --json`
- `memora source add <source.md> --extract <extract.md> --kind <kind> --project <project> --json`
- `memora review --json`
- `memora review approve <id...> --reason "<reason>" --json`
- `memora review reject <id...> --reason "<reason>" --json`

Do not edit vault files directly. If the needed operation is not listed, stop
and report the CLI gap.

## Setup And Health

`memora init <vault> [--set-default] [--wrapper PATH] [--json]`

- Create vault layout and `.memora/config.yaml`.
- With `--set-default`, also update the installed `memora` wrapper default.

`memora setup [vault] [--dry-run] [--json]`

- Preview or create default vault layout. Without `<vault>`, uses the configured
  default vault (`MEMORA_VAULT`) before falling back to the current directory.

`memora vault show [--wrapper PATH] [--json]`

- Show the default vault configured in the installed wrapper.

`memora vault set <vault> [--wrapper PATH] [--json]`

- Validate that `<vault>` is initialized, then set it as the installed wrapper default.

`memora help [--json]`

- Return public command groups. Useful for lightweight capability discovery.

`memora status [--vault PATH] [--json]`

- Summarize vault health and index state.

`memora doctor [--vault PATH] [--json]`

- Validate memory Markdown schema and graph links.

`memora reindex [--vault PATH] [--clean] [--json]`

- Rebuild local SQLite index from Markdown.

## Agent Integration

`memora agent rules [--client <client>] [--scope project|user] [--vault PATH] [--project NAME] [--alias NAME ...] [--json]`

- Generate Memora instructions for an agent client.

`memora agent integrate [--client <client>] [--scope project|user] [--project PATH] [--target PATH] [--vault PATH] [--alias NAME ...] [--dry-run] [--force] [--json]`

- Install generated instructions into a project/user target. For project scope,
  run from the target project or pass `--project PATH`; Memora refuses implicit
  writes from its own source checkout.
- User scope writes Claude to `~/.claude/CLAUDE.md` and Codex to
  `~/.codex/AGENTS.md`; Cursor user scope emits a fallback file under
  `~/.memora/integrations/` for manual installation because Cursor user rules
  are settings-managed.

`memora agent update [--client <client>] [--scope project|user] [--project PATH] [--target PATH] [--vault PATH] [--alias NAME ...] [--dry-run] [--force] [--json]`

- Update managed instruction blocks.

`memora agent status [--client <client>] [--scope project|user] [--project PATH] [--vault PATH] [--alias NAME ...] [--json]`

- Check installed instruction status.

`memora agent-aliases list [--vault PATH] [--json]`

- Show Remi-style assistant aliases.

`memora agent-aliases set <name...> [--vault PATH] [--json]`

- Persist assistant aliases.

## Raw And Sources

`memora raw add <path> --kind <kind> --format <format> [--title TEXT] [--project NAME] [--sensitivity normal|private|secret|unsafe] [--tag TAG ...] [--dry-run] [--vault PATH] [--json]`

- Copy raw input into staging with sidecar metadata. Does not create memories.

`memora raw list [path] [--vault PATH] [--json]`

- List staged raw files. Optional `path` defaults to vault `raw/`.

`memora raw inspect <path> [--vault PATH] [--json]`

- Inspect raw metadata and preview text when available.

`memora source add <source.md> [--extract <extract.md>] [--kind <kind>] [--format <format>] [--title TEXT] [--url URL] [--project NAME] [--sensitivity normal|private|secret|unsafe] [--tag TAG ...] [--vault PATH] [--json]`

- Save curated durable evidence under `Sources/`.

`memora lookup-source <source_id> [--query TEXT] [--budget N] [--session-id ID] [--loaded-source-id ID ...] [--vault PATH] [--json]`

- Return compact source evidence with citations.

## Memory Writes And Review

`memora remember --type <memory_type> --text TEXT [--scope user|project] [--project NAME] [--status <status>] [--tag TAG ...] [--vault PATH] [--json]`

- Create one canonical atomic memory.

`memora review [--group-by source] [--vault PATH] [--json]`

- List pending agent-authored memories.

`memora review approve <id...> [--reason TEXT] [--dry-run] [--override-unsafe] [--vault PATH] [--json]`

- Approve pending memories.

`memora review reject <id...> [--reason TEXT] [--dry-run] [--vault PATH] [--json]`

- Reject pending memories.

## Retrieval

`memora build-context <task> [--budget N] [--project NAME] [--task-class <class>] [--include-related] [--include-profile|--no-include-profile] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--loaded-source-id ID ...] [--refresh|--no-refresh] [--vault PATH] [--json]`

- Main agent recall command. Use returned context only when
  `memory_needed=true`.
- `--include-profile` adds a bounded in-memory rollup to this response.

`memora search <query> [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--created-after DATE] [--created-before DATE] [--updated-after DATE] [--updated-before DATE] [--valid-from DATE] [--valid-to DATE] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--refresh|--no-refresh] [--limit N] [--vault PATH] [--json]`

- Ranked search with snippets and citations.

`memora recall <query> [--budget N] [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--task-class <class>] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--loaded-source-id ID ...] [--refresh|--no-refresh] [--vault PATH] [--json]`

- Pack memory chunks under a token budget.

`memora brief <query> [--budget N] [--project NAME] [--type <memory_type>] [--status <status>] [--scope user|project] [--task-class <class>] [--include-related] [--semantic|--no-semantic] [--mode <mode>] [--session-id ID] [--loaded-memory-id ID ...] [--loaded-source-id ID ...] [--refresh|--no-refresh] [--vault PATH] [--json]`

- Produce citation-preserving Markdown context.

## Inspect And Open

`memora inspect <id> [--vault PATH] [--json]`

- Show one memory by id.

`memora open <id> [--launch] [--vault PATH] [--json]`

- Print memory path and Obsidian URI; optionally launch URI.

`memora conflicts [--vault PATH] [--json]`

- Detect Markdown sync conflicts that require manual resolution.

## Session Capture

`memora session finalize [transcript] [--transcript PATH] --summary-file <summary.md> [--memories-file <memories.json>] [--format TEXT] [--project NAME] [--tag TAG ...] [--sensitivity normal|private|secret|unsafe] [--confidence 0..1] [--dry-run] [--vault PATH] [--json]`

- Save session transcript/source, summary extract, and optional proposed
  memories for review.

