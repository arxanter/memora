# Memora

Memora is a small CLI-first memory core for coding agents. It keeps durable
facts, decisions, preferences, tasks, curated source evidence, and raw staging
files in an Obsidian-compatible Markdown vault, then gives agents compact cited
context through `memora ... --json`.

Agents should treat the CLI as the interface. They should not edit `Memories/`,
`Sources/`, `raw/`, or `.memora/` files directly.

## Install

Memora requires Python 3.10 or newer.

```bash
pipx install "memora"
memora setup ~/MemoryVault
```

From this repository:

```bash
./scripts/install.sh --vault ~/MemoryVault
export PATH="$HOME/.local/bin:$PATH"
```

For development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
```

On Windows, use WSL2 with Python 3.10 or newer and run the same commands from
the Linux environment. Vault lookup order is `--vault`, then `MEMORA_VAULT`,
then the nearest `.memora/config.yaml`.

## Quickstart

```bash
memora setup ~/MemoryVault --json

memora agent integrate \
  --client all \
  --project /path/to/repo \
  --vault ~/MemoryVault \
  --dry-run \
  --json

memora remember \
  --vault ~/MemoryVault \
  --type decision \
  --project my-project \
  --text "Use Markdown as durable memory; SQLite is rebuildable cache." \
  --json

memora build-context \
  "What did we decide about durable memory?" \
  --vault ~/MemoryVault \
  --project my-project \
  --task-class planning \
  --json
```

## Core Workflow

Memora is intentionally narrow:

```text
raw files -> memora raw add -> agent reads/analyzes
agent extract -> memora source add -> Sources/
durable atomic claim -> memora remember -> Memories/
user task -> memora build-context -> cited context
```

Use `raw/` for staging unprocessed files. Raw files are traceable input and may
be cleaned later.

```bash
memora raw add ./paper.pdf --kind pdf --format pdf --project my-project --json
memora raw add ./thread.json --kind slack --format json --project my-project --json
memora raw list --vault ~/MemoryVault --json
memora raw inspect raw/inbox/slack/thread.json --vault ~/MemoryVault --json
```

Use `Sources/` only for curated durable evidence worth keeping long-term. The
agent, not Memora, reads the raw material and writes the extract.

```bash
memora source add ./source.md \
  --extract ./extract.md \
  --kind text \
  --project my-project \
  --json
```

Use `remember` for small durable facts, decisions, preferences, tasks, or
project context.

```bash
memora remember --type preference --text "Prefer concise code review summaries." --json
memora remember --type task --project my-project --text "Document the session capture workflow." --json
```

## Find Context

Use `build-context` for normal agent recall. It first applies the recall policy
and returns no context when memory is not needed.

```bash
memora build-context "Plan the storage refactor" --project my-project --task-class planning --json
```

Use lower-level retrieval commands for explicit searches:

```bash
memora search "storage decision" --project my-project --json
memora recall "What did we decide about storage?" --project my-project --budget 1200 --json
memora brief "Prepare context for storage work" --project my-project --budget 1200 --json
memora lookup-source 2026-05-04_design-notes --query storage --json
```

## Agent Integration

Configure Remi aliases:

```bash
memora agent-aliases list --vault ~/MemoryVault --json
memora agent-aliases set Remi Рэми Реми --vault ~/MemoryVault --json
```

Generate or install rules for Cursor, Claude, Codex, or generic `AGENTS.md`:

```bash
memora agent rules --client cursor --vault ~/MemoryVault --project my-project
memora agent integrate --client all --project /path/to/repo --vault ~/MemoryVault --json
memora agent update --client all --project /path/to/repo --vault ~/MemoryVault --dry-run --json
memora agent status --client all --project /path/to/repo --json
```

When the user addresses the assistant as `Remi`, `Рэми`, or `Реми`, generated
rules route the request through Memora. The main toggles live in
`.memora/config.yaml` under `agent_policy`: `aliases`, `enabled`,
`auto_recall`, `session_capture`, `trust_level`, and recall budget.

## Session And Scheduled Capture

At the end of substantial work, an agent can save a transcript, a concise
summary, and proposed atomic memories:

```bash
memora session finalize ./cursor-session.jsonl \
  --summary-file ./session-summary.md \
  --memories-file ./session-memories.json \
  --project my-project \
  --json
```

Scheduled agents follow the same protocol: fetch with their own tools, call
`raw add`, write a concise extract, call `source add`, then save only durable
atomic memories with `remember`.

## Review

Agent-authored memories default to `pending`. Review them explicitly:

```bash
memora review --json
memora inspect mem_20260430_example --json
memora review approve mem_20260430_example --reason "verified source" --json
memora review reject mem_20260430_bad --reason "not durable" --json
```

## Vault Layout

```text
MemoryVault/
  raw/
  Sources/
  Memories/
  .memora/config.yaml
```

SQLite indexes, embeddings, caches, and locks under `.memora/` are rebuildable
local state. Do not sync or commit them.

```bash
memora doctor --vault ~/MemoryVault
memora reindex --vault ~/MemoryVault --clean
memora status --vault ~/MemoryVault --json
```

For technical details, see `docs/architecture.md`.
