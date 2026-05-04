# Memora

Memora is a local-first memory tool for coding agents. It keeps durable
facts, decisions, preferences, tasks, source extracts, and session summaries in
an Obsidian-compatible Markdown vault, then gives agents compact cited context
through the `memora` CLI.

The CLI is the stable interface. Agents should use `memora ... --json` instead
of editing vault files directly.

## Why Use It

- Keep project decisions and preferences outside a single chat session.
- Give agents only relevant context with `memora build-context`.
- Preserve source material before promoting durable memories.
- Review agent-written memory before it becomes active truth.
- Sync plain Markdown while treating SQLite, embeddings, locks, and caches as
  rebuildable local state.

## Install

Memora requires Python 3.10 or newer.

For a packaged install:

```bash
pipx install "memora"
memora setup ~/MemoryVault
```

From this repository:

```bash
./scripts/install.sh --vault ~/MemoryVault
export PATH="$HOME/.local/bin:$PATH"
```

Without cloning first, use a one-liner that `git clone`s into a directory you
keep (recommended under `$HOME`) or into `/tmp`; details and copy-paste commands
are in `docs/local-install.md` (section **One-liner: clone and install**).

For development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
```

### Windows And WSL

Memora is developed for macOS/Linux shells. On Windows, use WSL2 with Python
3.10 or newer and run the same install commands from the Linux environment.

Vault lookup order is `--vault`, then `MEMORA_VAULT`, then the nearest
`.memora/config.yaml`.

## Quickstart

Create a vault, connect an agent, save a decision, and recall it:

```bash
memora setup ~/MemoryVault --json

memora agent integrate \
  --client all \
  --project /path/to/repo \
  --vault ~/MemoryVault \
  --dry-run \
  --json

memora agent integrate \
  --client all \
  --project /path/to/repo \
  --vault ~/MemoryVault

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

`memora agent integrate --client all` installs generated rules for Cursor,
Claude, and Codex targets. Existing unmanaged instruction files are not
overwritten unless `--force` is passed.

## How Agents Use It

Generated agent rules teach coding agents to:

- call `memora build-context "<task>" --project "<project>" --json` only when
  memory is useful;
- use returned context only when `memory_needed` is `true`;
- preserve citations when memory affects an answer or decision;
- save raw/source material before creating atomic memories;
- leave inferred agent-created memories pending for review;
- avoid storing secrets, raw logs, and temporary implementation chatter.

Useful agent setup commands:

```bash
memora agent targets --client all --project /path/to/repo --json
memora agent status --client all --project /path/to/repo --json
memora agent update --client all --project /path/to/repo --dry-run --json
memora agent doctor --client all --project /path/to/repo --vault ~/MemoryVault --json
```

## Save Knowledge

Use `remember` for small durable facts, decisions, preferences, tasks, or project
context:

```bash
memora remember --type preference --text "Prefer concise code review summaries." --json
memora remember --type task --project my-project --text "Document the session capture workflow." --json
```

For larger material, save the source first and promote only durable atomic
memories:

```bash
memora import-source ./notes.md \
  --extract-file ./notes-extract.md \
  --project my-project \
  --json

memora import-url https://example.com/article --dry-run --json
memora import-pdf ./paper.pdf --text-file ./paper.txt --json
memora import-slack ./thread.json --channel "#project" --json
```

For end-of-session capture, let the agent write a concise summary and optional
memory proposals, then finalize:

```bash
memora session finalize ./cursor-session.jsonl \
  --summary-file ./session-summary.md \
  --memories-file ./session-memories.json \
  --project my-project \
  --json
```

## Find Context

Use `build-context` for normal agent recall. It first runs the automatic recall
policy and returns no context when memory is not needed:

```bash
memora build-context "Plan the storage refactor" --project my-project --task-class planning --json
```

Use lower-level retrieval commands when you are explicitly searching:

```bash
memora search "storage decision" --project my-project --json
memora recall "What did we decide about storage?" --project my-project --budget 1200 --json
memora brief "Prepare context for storage work" --project my-project --budget 1200 --json
```

## Review Memory

Agent-authored memories default to `pending`. Review them before they become
active durable context:

```bash
memora review --json
memora inspect mem_20260430_example --json
memora review approve mem_20260430_example --reason "verified source" --json
memora review reject mem_20260430_bad --reason "not durable" --json
```

Lifecycle commands keep old or conflicting memory explicit:

```bash
memora supersede mem_old --by mem_new --reason "decision changed" --json
memora contradict mem_a mem_b --reason "conflicting guidance" --json
memora mark mem_old --status stale --json
```

## Vault And Sync

The Markdown vault is durable state:

```text
MemoryVault/
  Memories/
  Sources/
  Briefs/
  Profiles/
  Synthesis/
  .memora/config.yaml
```

Do not sync or commit generated local state:

```gitignore
.memora/index.sqlite
.memora/cache/
.memora/embeddings/
.memora/locks/
```

After syncing a vault or resolving conflicts, rebuild local state:

```bash
memora conflicts --vault ~/MemoryVault
memora doctor --vault ~/MemoryVault
memora reindex --vault ~/MemoryVault --clean
```

## Sample Vault

Try the included fixture:

```bash
memora status --vault examples/sample-vault
memora review --vault examples/sample-vault --json
memora brief "What is the storage decision?" --vault examples/sample-vault --project memora --json
```

## Development

Run tests:

```bash
pytest
```

Run deterministic evaluation:

```bash
memora eval tests/fixtures/evaluation/coding-agent-questions.yaml --json
```

More detailed docs:

- `docs/commands.md`
- `docs/local-install.md`
- `docs/agent-instructions.md`
- `docs/schema.md`
- `docs/semantic-search.md`
- `docs/sync.md`
- `docs/evaluation.md`
