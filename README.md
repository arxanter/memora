# Agent Memory

Agent Memory is a local-first, Obsidian-backed memory engine for coding agents.
It stores durable memory as human-readable Markdown and builds compact,
citation-preserving context on demand for CLIs and MCP-compatible agents.

The project is aimed at agent context optimization, not generic note taking. The
important record is the Markdown vault you own and can inspect in Obsidian. Local
SQLite indexes, FTS data, embeddings, locks, and temporary caches are derived
state that can be deleted and rebuilt.

## Core Design

- Markdown and Obsidian are the durable source of truth. Memories live as
  Markdown files with YAML frontmatter, body text, observations, relations, and
  lifecycle metadata.
- SQLite FTS5 and embeddings are local cache. `memory reindex` rebuilds the
  cache from Markdown whenever you sync to a new machine or need recovery.
- CLI and MCP share the same services. Validation, Markdown writes, retrieval,
  recall, brief generation, lifecycle mutation, and token packing are not
  separate implementations.
- Recall is budgeted and citation-first. `memory recall` and `memory brief`
  rank, deduplicate, truncate when needed, and pack chunks under a strict
  estimated token budget. Every packed item includes a vault-relative citation.
- Agent-written memory is reviewable. Agent memories default to `pending`, while
  accepted memory is `active`. Stale, superseded, rejected, and contradicted
  memory is handled explicitly instead of being silently mixed into current
  context.

## Architecture

Recommended vault layout:

```text
MemoryVault/
  Memories/
    facts/
    preferences/
    decisions/
    tasks/
    sources/
    projects/
    conversations/
  Sources/
  Briefs/
  Profiles/
    projects/
  Synthesis/
  .agent-memory/
    config.yaml
    schemas/
    index.sqlite
    cache/
    embeddings/
    locks/
```

Typical data flow:

1. A user or agent writes a memory through `memory remember` or the MCP
   `remember` tool.
2. The shared schema layer validates frontmatter and writes Obsidian-compatible
   Markdown into `Memories/`.
3. `memory reindex` parses canonical Markdown into `.agent-memory/index.sqlite`,
   including documents, chunks, observations, graph links, and SQLite FTS5 data.
4. `memory search` retrieves ranked memory using deterministic query planning,
   fallback variants, keyword search, metadata filters, lifecycle state, graph
   signals, and optional semantic vectors.
5. `memory recall` packs ranked chunks under a strict token budget and returns
   citations for each chunk.
6. `memory brief` renders the packed recall output into stable agent-facing
   sections: current facts, current decisions, warnings, open questions, and
   citations.
7. MCP clients usually call `build_context`, which first runs the deterministic
   `should_recall` policy and only builds a brief when memory is useful.

## Installation

The package requires Python 3.10 or newer. For a packaged install, use `pipx` so
the `memory` CLI lives in an isolated environment:

```bash
pipx install "agent-memory[mcp]"
```

For this repository, the local installer is the quickest machine setup. It
creates stable wrapper commands without requiring you to activate a venv:

```bash
./scripts/install.sh --vault ~/MemoryVault
export PATH="$HOME/.local/bin:$PATH"
```

This creates stable `memory`, `memory-mcp`, and `agent-memory-service` wrapper
commands. It supports macOS, Linux, and WSL2. See `docs/local-install.md` for
service management, MCP activation, upgrade, and uninstall details.

For development and local CLI usage from a clone, use an editable install:

```bash
cd /path/to/memory-project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
```

Install the optional MCP dependency when you want to run the MCP server:

```bash
python -m pip install -e '.[mcp]'
```

After installation, these console scripts are available in the active
environment:

```bash
memory --help
memory-mcp
```

## Quickstart

Create a vault, install agent rules, add memory, and recall context:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'

memory setup ./memory-vault --dry-run
memory setup ./memory-vault
memory agent-rules --format cursor --vault ./memory-vault --project agent-memory
memory install-agent-rules --client cursor --project /path/to/repo --dry-run

memory remember --vault ./memory-vault --type decision --text "Use Markdown as durable memory."
memory reindex --vault ./memory-vault

memory search --vault ./memory-vault "durable memory"
memory recall --vault ./memory-vault "What did we decide about durable memory?" --budget 1200
memory brief --vault ./memory-vault "Prepare context for work on memory storage." --budget 1200
memory should-recall "What did we decide about durable memory?"
```

Most commands also support `--json` for agent-friendly output:

```bash
memory brief --vault ./memory-vault "storage decisions" --budget 1200 --json
```

To preserve source material before promoting durable memories:

```bash
memory import-source ./notes.md --vault ./memory-vault --extract-file ./notes-extract.md --project agent-memory --json
memory import-url https://example.com/article --vault ./memory-vault --dry-run --json
memory import-pdf ./paper.pdf --vault ./memory-vault --text-file ./paper.txt --json
memory import-zoom ./meeting-summary.md --vault ./memory-vault --project agent-memory --json
memory import-slack ./thread.json --vault ./memory-vault --channel "#agent-memory" --json
memory source-inbox scan --vault ./memory-vault --path ./raw/inbox --ignore-disabled --dry-run --json
```

Review and curate agent-written memory before it becomes active durable context:

```bash
memory review --vault ./memory-vault
memory review approve mem_20260430_example --vault ./memory-vault --reason "verified source"
memory reject --vault ./memory-vault mem_20260430_bad --reason "Not durable enough"
memory synthesize "raw ingestion" --vault ./memory-vault --project agent-memory --dry-run --json
```

When a vault is already configured, commands resolve it in this order:

1. The explicit `--vault` option.
2. The `AGENT_MEMORY_VAULT` environment variable.
3. The nearest parent `.agent-memory/config.yaml`.

## Sample Vault

`examples/sample-vault` is a ready CLI-first fixture with `.agent-memory/config.yaml`,
raw inbox material, imported `Sources/`, source-backed `Memories/`, graph
wikilinks, and pending review examples:

```bash
memory status --vault examples/sample-vault
memory review --vault examples/sample-vault
memory brief "What is the storage decision?" --vault examples/sample-vault --project agent-memory --json
memory source-inbox scan --vault examples/sample-vault --path examples/sample-vault/raw/inbox --ignore-disabled --dry-run --json
```

Connector config is disabled by default in the sample. Use explicit import
commands or `--ignore-disabled` for one-shot dry runs.

## Windows And WSL

Native Windows support is not first-class yet. Use WSL2 with Python 3.10 or newer
and install/run `memory` inside the Linux environment. Prefer a vault path inside
the WSL filesystem, such as `~/MemoryVault`, for better file watching and path
behavior. If you keep the vault on the Windows side for Obsidian, pass the WSL
path form, for example `/mnt/c/Users/you/Documents/MemoryVault`, and avoid mixing
Windows-style paths in CLI config.

## MCP Usage

Install the MCP extra and point the server at a vault:

```bash
python -m pip install -e '.[mcp]'
memory init ~/MemoryVault
export AGENT_MEMORY_VAULT=~/MemoryVault
memory-mcp
```

Compact client configuration example:

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

If `memory-mcp` is not on the client process `PATH`, use the module form:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "python",
      "args": ["-m", "agent_memory.mcp_server"],
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

Primary MCP tools include `remember`, `search`, `recall`, `brief`,
`should_recall`, `build_context`, `save_source`, `ingest_url`, `inspect`,
`explain_recall`, `mark_status`, and `mark_superseded`. `build_context` is the
recommended entry point for agents: it avoids spending context when memory is not
relevant, and otherwise returns a citation-preserving Memory Brief.

`save_source` and `ingest_url` preserve raw material and the agent-written
extract under `Sources/`; the AI agent still fetches, reads, and calls
`remember` only for separate durable atomic facts, decisions, preferences,
project context, or tasks.
See `docs/agent-instructions.md` for Claude/Cursor/Codex instruction templates.

To print the MCP client snippet again:

```bash
memory mcp-config
memory mcp-config --format claude
memory mcp-config --format cursor
```

See `docs/mcp-integrations.md` for client-specific notes for Codex, Claude Code,
Cursor, and custom MCP clients.

## Semantic Search

Semantic search is optional and disabled by default. Keyword FTS search works
without embeddings. The default retrieval mode is `auto`: it uses hybrid
keyword-plus-vector search when a semantic provider is configured, and text-only
search otherwise.

For production use under the current project constraint, embeddings must come
from the same AI model/session that the user is interacting with. The standalone
CLI and MCP server cannot currently access Cursor's active session embeddings,
so the recommended path is to leave semantic search disabled and use `auto`
mode's text/query-planning fallback.

Agent Memory does not ship first-class OpenAI, Ollama, FastEmbed, or other
public/open/local model providers. It keeps a pre-existing generic command
protocol for compatibility, but this is not the recommended production path
unless the command is backed by an approved same-session embedding bridge:

```yaml
semantic:
  provider: local-command
  model: same-session-model
  command:
    - ./scripts/embed-session
  timeout_seconds: 30
  vector_limit: 100
  keyword_limit: 100
```

The command receives JSON on stdin and returns embeddings on stdout. Missing or
stale vectors are generated lazily during semantic search, and stored vectors
remain rebuildable cache data.

```bash
memory reindex --vault ./memory-vault
memory search --vault ./memory-vault "agent memory retrieval" --mode auto
memory search --vault ./memory-vault "agent memory retrieval" --no-semantic
```

Available modes are `auto`, `text`, `vector`, and `hybrid`. The legacy
`--semantic/--no-semantic` switch still works, mapping to `hybrid` and `text`.
`memory search --json`, `memory recall --json`, `memory brief --json`, and MCP
`build_context` include compact trace metadata with planned query variants,
attempted searches, mode, semantic status/provider/model, selected count, and an
empty reason when no context was selected. See `docs/semantic-search.md` for
the current-session limitation, the generic provider hook, environment overrides,
and the deterministic test-only provider.

## Sync Model

Sync the Markdown vault. Do not sync local generated state.

Good sync candidates:

- `Memories/**/*.md`
- `Sources/**/*.md`
- `Briefs/**/*.md`
- `Profiles/**/*.md`
- `Synthesis/**/*.md`
- `.agent-memory/config.yaml`
- `.agent-memory/schemas/`

Do not sync or commit:

```gitignore
.agent-memory/index.sqlite
.agent-memory/cache/
.agent-memory/embeddings/
.agent-memory/locks/
**/.agent-memory/index.sqlite
**/.agent-memory/cache/
**/.agent-memory/embeddings/
**/.agent-memory/locks/
```

The repository `.gitignore` already includes these cache rules. After syncing a
vault to another machine, or after resolving file-sync conflicts, rebuild local
state:

```bash
memory reindex --vault /path/to/vault --clean
```

Use `memory conflicts` and `memory doctor` to find conflict markers, duplicate
memory IDs, invalid frontmatter, and graph issues before rebuilding.

## Lifecycle And Review

Every memory has a lifecycle status:

- `pending`: proposed memory awaiting review.
- `active`: current memory eligible for normal recall.
- `stale`: old or lower-confidence memory that may appear as a warning.
- `superseded`: replaced by newer memory and hidden by default.
- `rejected`: explicitly not accepted and hidden by default.

Agent-authored memories default to `pending`. A typical review workflow is:

```bash
memory review --vault ./memory-vault
memory inspect --vault ./memory-vault mem_20260430_example
memory mark --vault ./memory-vault mem_20260430_example --status active
memory reject --vault ./memory-vault mem_20260430_bad --reason "Not durable enough"
memory reindex --vault ./memory-vault
```

Lifecycle relations are durable Markdown metadata, not only index state:

```bash
memory supersede --vault ./memory-vault mem_old --by mem_new --reason "Decision changed"
memory contradict --vault ./memory-vault mem_a mem_b --reason "Conflicting guidance"
memory mark --vault ./memory-vault mem_old --status stale
memory decay --vault ./memory-vault
```

Default retrieval includes `active` and `stale` memory, excludes `pending`,
`rejected`, and `superseded` memory, and surfaces stale or contradictory context
as warnings when selected for a brief.

## Testing And Evaluation

Run the test suite:

```bash
pytest
```

Run the deterministic evaluation fixture:

```bash
memory eval tests/fixtures/evaluation/coding-agent-questions.yaml --json
```

The evaluation harness copies fixture vaults to a temporary location, performs a
clean reindex, and checks search, recall, brief, review, conflict, or doctor
cases against expected memory IDs, warning behavior, token budgets, and
citations.

## Command Overview

Core setup and health commands:

- `memory init <vault>` creates the vault layout and `.agent-memory/config.yaml`.
- `memory setup [vault]` previews or creates the vault layout and next setup
  steps, with `--dry-run` for no-write planning.
- `memory agent-rules` generates CLI-first instructions for AGENTS.md, Cursor,
  Claude, or Codex.
- `memory install-agent-rules` installs generated rules into a project file with
  dry-run and no-overwrite safeguards.
- `memory status` summarizes memory count, pending count, issue count, and index
  presence.
- `memory doctor` validates memory Markdown and graph targets.
- `memory conflicts` detects sync conflict markers, duplicate IDs, and invalid
  frontmatter.
- `memory reindex` rebuilds the local SQLite index from Markdown.

Write and review commands:

- `memory remember` creates a validated Markdown memory.
- `memory import-source` saves Markdown/text source material and an optional
  extract under `Sources/` without promoting canonical memory.
- `memory import-source-inbox` imports Markdown/text files from a source inbox
  directory, with `--dry-run` for safe preview.
- `memory import-session` saves AI-agent transcripts under `Sources/` and can
  create a pending `conversation_summary` from a supplied summary file.
- `memory review` lists pending agent-generated memories.
- `memory mark` changes lifecycle status.
- `memory reject` rejects a memory.
- `memory supersede` marks an older memory as replaced by a newer one.
- `memory contradict` records a contradiction relation.
- `memory decay` marks expired active memories stale.

Retrieval and agent-context commands:

- `memory search` returns ranked memory-level results with snippets and
  citations.
- `memory recall` returns ranked chunks packed under a token budget.
- `memory explain-recall` explains selected and skipped recall candidates.
- `memory brief` renders a citation-preserving Memory Brief.
- `memory should-recall` decides whether a user request should use memory.

Inspection and compatibility commands:

- `memory inspect` shows one memory by ID.
- `memory open` prints the Markdown path and Obsidian URI.
- `memory graph` shows incoming and outgoing relations.
- `memory eval` runs fixture-backed evaluation cases.
- `memory import` and `memory export` are placeholder compatibility commands for
  Markdown and Basic Memory-like workflows.

For full command details and options, see `docs/commands.md`.

## Troubleshooting

Missing or stale index:

```bash
memory reindex --vault /path/to/vault
memory reindex --vault /path/to/vault --clean
```

No config found:

- Pass `--vault /path/to/vault`.
- Or set `AGENT_MEMORY_VAULT=/path/to/vault`.
- Or run commands from inside a vault containing `.agent-memory/config.yaml`.

MCP dependency errors:

```bash
python -m pip install -e '.[mcp]'
```

Then restart the agent client so it launches the MCP server from the updated
environment.

Sync conflicts or duplicate IDs:

```bash
memory conflicts --vault /path/to/vault
memory doctor --vault /path/to/vault
memory reindex --vault /path/to/vault --clean
```

Resolve Markdown conflicts manually before rebuilding the index.

Local virtualenv or test dependency issues:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
pytest
```

For deeper design docs, start with `docs/spec.md`, `docs/schema.md`,
`docs/commands.md`, `docs/local-install.md`, `docs/mcp-integrations.md`,
`docs/agent-instructions.md`, `docs/semantic-search.md`, `docs/sync.md`, and
`docs/evaluation.md`.
