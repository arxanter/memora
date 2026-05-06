# Memora CLI Reference

Memora is a CLI-first local memory vault for coding agents. Commands operate on
the Memora home resolved from `MEMORA_HOME`, or `~/.memora` when the environment
variable is not set.

The public CLI intentionally has no `--home` flag. Use `MEMORA_HOME=/path/to/home`
for custom installs, tests, or temporary vaults.

## Common Values

- `--mode`: `auto`, `text`, `vector`, `hybrid`
- `--intent`: `auto`, `memory`, `wiki`, `evidence`, `mixed`
- `--client`: `all`, `agents`, `cursor`, `claude`, `codex`
- `--scope`: `project`, `user`
- Memory types: `fact`, `decision`, `preference`, `task`, `project_context`,
  `conversation_summary`
- Memory scopes: `user`, `project`, `global`
- Memory statuses: `pending`, `active`, `stale`, `superseded`, `rejected`
- Raw kinds: `pdf`, `zoom`, `slack`, `text`, `webclip`, `article`
- Raw formats: `pdf`, `markdown`, `json`, `txt`
- Sensitivity labels: `normal`, `private`, `secret`
- Wiki page types: `source`, `entity`, `concept`, `synthesis`

## Data Contracts

Agents should use file staging for large write inputs. Create temporary
`.md`, `.yaml`, `.yml`, `.json`, or other payload files under the project
`.memora/` directory or user `~/.memora/temp/`, then pass the file path to Memora.
Memora copies or ingests the content and does not delete input files. After a
successful command, the agent should remove temporary staged files itself. Small
values can still be passed directly as CLI arguments.

Memory files are Markdown with YAML frontmatter containing
`schema_version: 1`, `id`, `type`, `scope`, optional `project`, `status`,
optional `confidence` between `0` and `1`, `created_at`, `updated_at`,
optional `source`, optional `author`, optional `relations`, and optional
`tags`. Agent-generated memories must include `source` and `confidence`, and
should normally be saved as `pending` until reviewed.

Raw sidecars are `<file>.meta.yaml` files with `raw_id`, `kind`, `format`,
`title`, `tags`, `sensitivity`, `captured_at`, `original_path`, `file_name`,
`size_bytes`, `content_hash`, and optional processing fields.

For PDFs, `raw add` preserves the original PDF in `raw/inbox/pdf/` with a
`.meta.yaml` sidecar. Create a text or Markdown extract/summary before
promoting the material to sources, wiki, or memory.

Source files are Markdown with YAML frontmatter containing `schema_version: 1`,
`source_id`, `kind` (`source` or `extract`), `title`, `captured_at`, `channel`,
`source_quality`, `sensitivity`, optional `url`, `tags`, `risk_flags`, and
`origin`.

Wiki pages are Markdown with optional YAML frontmatter: `title`, `type`,
`source_id`, `sources`, `entities`, `concepts`, and `last_updated`.

## Setup And Health

### `memora setup`

Creates the managed home structure, including `vault/`, `state/`, `bin/`,
default config, raw inbox folders, memory folders, source folders, and wiki
folders.

Arguments:

- `--dry-run`: print resolved paths without writing files.

### `memora status`

Prints resolved home, vault, config, state, index, and managed binary status.

### `memora doctor`

Validates memories, raw sidecars, sources, and wiki pages. Prints `doctor: ok`
when no issues are found.

### `memora reindex`

Rebuilds the local SQLite index used by search, probe, and context.

Arguments:

- `--clean`: remove the existing index file before rebuilding.

## Managed Binary And Shell Integration

### `memora self install`

Installs a Memora binary into `$MEMORA_HOME/bin/memora`, creates the home if
needed, and installs or repairs the shell startup managed block for supported
shells.

Arguments:

- `--from <PATH>`: install this binary instead of the current executable.
- `--sha256 <SHA256>`: verify the binary hash before installing.
- `--force`: overwrite an existing managed binary.
- `--dry-run`: print planned writes without changing files.
- `--no-shell-integration`: skip shell startup integration.

Environment:

- `MEMORA_SHELL_INTEGRATION=0`: disables shell startup integration.

### `memora self update`

Updates `$MEMORA_HOME/bin/memora` while preserving the vault, and repairs shell
startup integration if it is missing or stale. Without `--from`, it downloads
the platform-specific binary and `SHA256SUMS` from GitHub Releases, verifies the
checksum, and installs the downloaded binary.

Arguments:

- `--from <PATH>`: update from this local binary instead of GitHub Releases.
- `--sha256 <SHA256>`: verify the local or downloaded binary with this hash;
  when omitted for GitHub downloads, the release `SHA256SUMS` entry is used.
- `--repo <OWNER/REPO>`: GitHub repository to download from when `--from` is
  omitted. Default: `arxanter/memora`. Env: `MEMORA_REPO`.
- `--version <TAG>`: GitHub release tag to download when `--from` is omitted.
  Default: `latest`. Env: `MEMORA_VERSION`.
- `--dry-run`: print planned writes without changing files.
- `--no-shell-integration`: skip shell startup integration.

### `memora self shell-init <SHELL>`

Prints shell commands that export `MEMORA_HOME`, set `FASTEMBED_CACHE_DIR`, add
Memora to `PATH`, and install the `memora` alias.

Arguments:

- `<SHELL>`: shell syntax to print, such as `zsh`, `bash`, `fish`, or
  `powershell`.

### `memora self completions <SHELL>`

Prints shell completion scripts.

Arguments:

- `<SHELL>`: shell completion format to generate.

### `memora uninstall`

Removes generated state and the managed binary. The vault is preserved by
default.

Arguments:

- `--remove-vault`: also remove the vault and config.
- `--dry-run`: print removal targets without deleting files.

## Agent Integration

Supported `--client` values:

- `all`: install Cursor, Claude, and Codex instructions.
- `cursor`: write Cursor rules.
- `claude`: write Claude instructions.
- `codex`: write Codex instructions.
- `agents`: write generic agent instructions.

Agent rules are written only inside Memora managed blocks. Project-scope targets:

- Cursor: `.cursor/rules/memora.mdc`
- Claude: `CLAUDE.md`
- Codex and generic agents: `AGENTS.md`

User-scope targets:

- Cursor: `~/.cursor/rules/memora.mdc`
- Claude: `~/.claude/CLAUDE.md`
- Codex: `~/.codex/AGENTS.md`
- Generic agents: `~/.memora/AGENTS.md`

### `memora agent rules`

Prints generated agent instructions without writing files.

Arguments:

- `--client <CLIENT>`: client to render for. Default: `all`.
- `--scope <SCOPE>`: scope to describe. Default: `project`.

### `memora agent reference`

Prints the detailed command and data-format reference embedded into generated
agent instructions. Use this when an agent needs exact allowed values, output
shapes, or JSON/YAML contracts without reading the managed rule file.

### `memora agent integrate`

Installs generated rules into client-specific instruction files.

Arguments:

- `--client <CLIENT>`: client to install. Default: `all`.
- `--scope <SCOPE>`: install project or user rules. Default: `project`.
- `--project <DIR>`: project directory for project-scoped targets.
- `--target <PATH>`: explicit output file; overrides normal target resolution.
- `--dry-run`: print planned writes without changing files.
- `--force`: append a fresh block if an existing managed block is partial.

### `memora agent update`

Refreshes managed rule blocks using the same arguments as `agent integrate`.

### `memora agent status`

Reports whether generated rule blocks are installed and current.

Arguments:

- `--client <CLIENT>`: client to inspect. Default: `all`.
- `--scope <SCOPE>`: scope to inspect. Default: `project`.
- `--project <DIR>`: project directory for project-scoped targets.

### `memora agent-aliases list`

Prints configured explicit memory trigger aliases, one per line.

### `memora agent-aliases set <NAME...>`

Replaces the configured alias list.

Arguments:

- `<NAME...>`: aliases such as `Remi`, `Рэми`, `Реми`.

After changing aliases, run `memora agent update` to refresh installed rules.

## Raw And Source Capture

### `memora raw add <PATH>`

Copies a raw file into the managed raw inbox with metadata.

Arguments:

- `<PATH>`: local file to stage.
- `--kind <KIND>`: raw kind. Allowed values: `pdf`, `zoom`, `slack`, `text`,
  `webclip`, `article`.
- `--format <FORMAT>`: raw input format. Allowed values: `pdf`, `markdown`,
  `json`, `txt`.
- `--title <TITLE>`: human-readable title.
- `--sensitivity <LEVEL>`: sensitivity label. Allowed values: `normal`,
  `private`, `secret`. Default: `normal`.
- `--tag <TAG>`: tag to attach; repeat for multiple tags.
- `--dry-run`: print planned destination and metadata without writing files.

### `memora raw analyze <PATH>`

Prepares an extract draft for raw material and scans for basic risk flags.

Arguments:

- `<PATH>`: raw file to analyze.
- `--output <PATH>`: write the extract draft to a specific path.
- `--overwrite`: overwrite an existing extract draft.
- `--dry-run`: print planned analysis output without writing files.

### `memora raw list [PATH]`

Lists raw files under the raw area or under an optional path.

### `memora raw inspect <PATH>`

Prints one raw file and its sidecar metadata.

### `memora raw mark-processed <PATH>`

Moves a raw file to `raw/processed`.

Arguments:

- `<PATH>`: raw file to move.
- `--source-id <SOURCE_ID>`: curated source created from this raw material.
- `--dry-run`: print the planned move without changing files.

### `memora source add <PATH>`

Adds curated source evidence and optional extract content.

Arguments:

- `<PATH>`: source file or raw file to preserve.
- `--extract <PATH>`: concise extract file.
- `--kind <KIND>`: source channel stored as frontmatter `channel`. Common
  values: `file`, `ai_session`, `slack`, `webclip`.
- `--format <FORMAT>`: source origin format. Common values: `markdown`,
  `json`, `txt`, `pdf`. Default: `markdown`.
- `--title <TITLE>`: human-readable title.
- `--url <URL>`: original source URL.
- `--sensitivity <LEVEL>`: sensitivity label. Allowed values: `normal`,
  `private`, `secret`.
- `--tag <TAG>`: tag to attach; repeat for multiple tags.

### `memora lookup-source <SOURCE_ID>`

Reads a curated source.

Arguments:

- `<SOURCE_ID>`: source id to read.
- `--query <TEXT>`: optional query printed with the result.
- `--budget <N>`: approximate character budget. Default: `800`.

## Wiki

### `memora wiki status`

Prints wiki page counts and storage paths.

### `memora wiki read <TARGET>`

Reads a wiki page by page key, title, or relative path.

Arguments:

- `<TARGET>`: page key, title, or relative path.
- `--full`: return the full page instead of a compact excerpt.
- `--max-chars <N>`: maximum characters to print.

### `memora wiki search <QUERY>`

Searches wiki pages.

Arguments:

- `<QUERY>`: wiki search query.
- `--limit <N>`: maximum number of wiki results.

### `memora wiki ingest <SOURCE_ID>`

Ingests a curated source into wiki source/entity/concept pages.

Arguments:

- `<SOURCE_ID>`: curated source id.
- `--title <TITLE>`: title for a source wiki page.
- `--entity <NAME>`: entity page to update; repeat for multiple entities.
- `--concept <NAME>`: concept page to update; repeat for multiple concepts.

### `memora wiki synthesize <QUESTION>`

Synthesizes an answer from saved knowledge.

Arguments:

- `<QUESTION>`: question to answer.
- `--title <TITLE>`: title to use when saving.
- `--save`: save the synthesis as a wiki page.
- `--limit <N>`: maximum candidates to use.

### `memora wiki lint`

Validates wiki page frontmatter and links.

## Memories And Review

### `memora remember`

Creates one atomic memory.

Arguments:

- `--type <TYPE>`: memory type. Allowed values: `fact`, `decision`,
  `preference`, `task`, `project_context`, `conversation_summary`.
- `--text <TEXT>`: memory body.
- `--text-file <PATH>`: read memory body from a staged text/Markdown/YAML file.
  Use either `--text` or `--text-file`.
- `--scope <SCOPE>`: memory scope. Allowed values: `user`, `project`,
  `global`. `project` scope requires `--project`.
- `--project <PROJECT>`: project key.
- `--status <STATUS>`: review status. Allowed values: `pending`, `active`,
  `stale`, `superseded`, `rejected`. Default: `pending`.
- `--tag <TAG>`: tag to attach; repeat for multiple tags.

### `memora memory update <MEMORY_ID>`

Updates memory metadata or body text.

Arguments:

- `<MEMORY_ID>`: memory id to update.
- `--type <TYPE>`: replace memory type. Allowed values: `fact`, `decision`,
  `preference`, `task`, `project_context`, `conversation_summary`.
- `--scope <SCOPE>`: replace memory scope. Allowed values: `user`, `project`,
  `global`.
- `--project <PROJECT>`: replace project key.
- `--clear-project`: remove project key.
- `--status <STATUS>`: replace review status. Allowed values: `pending`,
  `active`, `stale`, `superseded`, `rejected`.
- `--confidence <FLOAT>`: replace confidence score, `0.0` through `1.0`.
- `--clear-confidence`: remove confidence score.
- `--tag <TAG>`: replace tags with repeated values.
- `--clear-tags`: remove all tags.
- `--title <TITLE>`: replace title.
- `--clear-title`: remove title.
- `--text <TEXT>`: replace memory body.
- `--text-file <PATH>`: replace memory body with text read from a staged
  text/Markdown/YAML file. Use either `--text` or `--text-file`.
- `--reason <TEXT>`: reason to append to update history.
- `--dry-run`: print the updated memory without writing it.

### `memora review list`

Lists pending review items.

Arguments:

- `--group-by <FIELD>`: optional grouping field. Supported values: `type`,
  `source`.

### `memora review approve <ID...>`

Approves pending memories.

Arguments:

- `<ID...>`: memory ids to approve.
- `--reason <TEXT>`: reason to record.
- `--dry-run`: print decisions without writing changes.

### `memora review reject <ID...>`

Rejects pending memories. Arguments match `review approve`.

## Retrieval

### `memora search <QUERY>`

Searches memories with text, vector, or hybrid retrieval.

Arguments:

- `<QUERY>`: search query.
- `--variant <QUERY>`: additional query variant; repeat for synonyms or
  translations.
- `--project <PROJECT>`: restrict results to a project key.
- `--type <TYPE>`: restrict memory type. Allowed values: `fact`, `decision`,
  `preference`, `task`, `project_context`, `conversation_summary`.
- `--status <STATUS>`: restrict review status. Allowed values: `pending`,
  `active`, `stale`, `superseded`, `rejected`.
- `--scope <SCOPE>`: restrict memory scope. Allowed values: `user`, `project`,
  `global`.
- `--limit <N>`: maximum number of results.
- `--mode <MODE>`: `auto`, `text`, `vector`, or `hybrid`. Default: `auto`.
- `--include-related`: expand direct matches through indexed relations.

### `memora probe <QUERY>`

Agent discovery command for compact routing across memories and wiki. `probe`
does not search curated source bodies.

Arguments:

- `<QUERY>`: discovery query.
- `--variant <QUERY>`: additional query variant.
- `--project <PROJECT>`: restrict memory results to a project key.
- `--intent <INTENT>`: `auto`, `memory`, `wiki`, `evidence`, or `mixed`.
  Default: `auto`.
- `--load`: reserved for future loaded output; current output remains compact.
- `--mode <MODE>`: memory retrieval mode. Default: `auto`.
- `--include-related`: expand memory matches through relations.

### `memora context <QUERY>`

Builds compact task context across memories, wiki, and sources.

Arguments:

- `<QUERY>`: task or question to build context for.
- `--variant <QUERY>`: additional query variant.
- `--project <PROJECT>`: restrict memory results to a project key.
- `--intent <INTENT>`: `auto`, `memory`, `wiki`, `evidence`, or `mixed`.
  Default: `auto`.
- `--budget <CHARS>`: approximate packed-context character budget.
  Default: `1200`.
- `--load`: reserved for future loaded output.
- `--mode <MODE>`: memory retrieval mode. Default: `auto`.
- `--include-related`: expand memory matches through relations.

### `memora inspect <MEMORY_ID>`

Shows one memory by id.

### `memora open <MEMORY_ID>`

Prints the file path for a memory.

Arguments:

- `--launch`: open the file with the platform file opener.

## Session Capture

### `memora session finalize [TRANSCRIPT]`

Captures a completed session source and proposed pending memories.

Arguments:

- `[TRANSCRIPT]`: optional transcript file to preserve as a source.
- `--summary-file <PATH>`: required session summary file.
- `--memories-file <PATH>`: optional JSON array of proposed memories. Each item
  can be a string or an object shaped as
  `{"type":"decision","text":"...","tags":["tag"]}`.
- `--project <PROJECT>`: project key.
- `--tag <TAG>`: tag to attach; repeat for multiple tags.
- `--dry-run`: validate inputs and print planned actions without writing files.

## Environment

- `MEMORA_HOME`: overrides the default `~/.memora` home.
- `MEMORA_SHELL_INTEGRATION=0`: disables shell startup integration during
  `self install` and `self update`.
- `MEMORA_SEMANTIC_PROVIDER`: overrides semantic provider. Use `none`,
  `fastembed`, `local-command`, or `deterministic`.
- `MEMORA_SEMANTIC_MODEL`: overrides semantic model.
- `MEMORA_SEMANTIC_COMMAND`: command for the `local-command` provider.
- `FASTEMBED_CACHE_DIR`: set by `memora self shell-init`; Memora also passes an
  explicit managed cache path to fastembed internally.
