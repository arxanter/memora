# Memora

Memora is a local CLI-first memory vault for coding agents.

It is built around a few simple principles:

- **Local-first storage**: project knowledge lives in a local YAML/Markdown vault, not in an external service.
- **Traceable knowledge**: raw sources can be captured close to the original material, then turned into reviewed memories, source evidence, or wiki notes.
- **Agent-native recall**: agents can look up project history, decisions, preferences, TODOs, and saved evidence when a task needs context.
- **Semantic search**: Memora keeps a rebuildable local index and supports hybrid text/vector retrieval through `fastembed`.
- **Managed agent instructions**: Cursor, Claude, Codex, and generic agents can receive generated rules for when and how to use memory.
- **Review before persistence**: agents are instructed to show proposed memories, extractions, and synthesized values before saving unless review is explicitly waived.

## Quick Start

### 1. Install

```bash
# Latest release
curl -fsSL https://raw.githubusercontent.com/arxanter/memora/main/scripts/install.sh | bash

# Overwrite an existing managed binary
curl -fsSL https://raw.githubusercontent.com/arxanter/memora/main/scripts/install.sh | bash -s -- --force

# Specific release
curl -fsSL https://raw.githubusercontent.com/arxanter/memora/main/scripts/install.sh | bash -s -- --version v0.1.0
```

The installer verifies `SHA256SUMS`, installs to `~/memora/bin/memora`, initializes the Memora home, and adds shell integration. Open a new shell or run the activation command printed by the installer.

### 2. Configure Agents

```bash
# Current project
memora agent integrate --client all --scope project
memora agent status --client all --scope project

# User-level/global instructions
memora agent integrate --client all --scope user
memora agent status --client all --scope user
```

Supported `--client` values:

- `all`: Cursor, Claude, and Codex.
- `cursor`: Cursor rules in `.cursor/rules/memora.mdc`.
- `claude`: Claude instructions in `CLAUDE.md`.
- `codex`: Codex instructions in `AGENTS.md`.
- `agents`: generic agent instructions in `AGENTS.md`.

Agent instructions are written only inside Memora managed blocks. User-scope targets use matching global files under the user's home directory.

### 3. Update

```bash
# Latest release
memora self update

# Specific release
memora self update --version v0.1.0

# Refresh generated agent instructions after an update
memora agent update --client all --scope project
memora agent update --client all --scope user
```

Updates preserve the vault, verify release checksums, and repair shell integration when needed.

## Daily Usage

Use an alias such as `Remi`, `Рэми`, or `Реми` when you explicitly want memory behavior:

```text
Remi, remember that this project prefers small focused PRs with nearby tests.
Remi, save this as a project decision: we use project-scoped Cursor rules.
Remi, review pending memories.
```

Ask Memora to capture and analyze useful source material:

```text
Remi, analyze this article and extract what matters for our agent memory roadmap.
Remi, save this incident write-up as source evidence and propose candidate memories.
Remi, read this design doc, summarize it, and ask me before saving anything.
```

Ask Memora to recall prior knowledge:

```text
Remi, find what we decided about semantic search providers.
Remi, what do we know about pending memory review UX?
Remi, find source evidence for the install flow decisions.
```

Agents must show the exact memory, extraction, source value, or synthesis before saving it unless you explicitly say review is not required. During review, agents should show pending notes clearly and let you approve, reject, or edit-and-approve each note.

Agents may also auto-recall context without an alias when a task depends on project history, preferences, decisions, roadmap/status/TODOs, wiki knowledge, or saved source evidence. They should not query memory on every turn.

## How Agents Use Memora

<details open>
<summary>Source Capture and Semantic Search</summary>

### Source Capture

Agents use source capture for material that should remain traceable: articles, notes, meeting summaries, design docs, incident write-ups, and research snippets.

```bash
memora raw add notes.md --kind text --format markdown
memora raw analyze raw/inbox/text/notes.md
memora source add /path/to/raw --extract /path/to/extract.md
memora raw mark-processed raw/inbox/text/notes.md --source-id <source_id>
```

Raw material should stay as close to the original as possible, preferably with no text changes. Agents should only move it into a convenient file/format for capture. `raw analyze` drafts an extraction under `raw/analysis/`, flags basic risks, and prints next steps.

Agents use `memora wiki ingest` for curated source material and `memora wiki synthesize --save` for durable wiki knowledge. Saving still requires approval by default.

### Semantic Search

New homes default to local `fastembed` semantic search:

```yaml
semantic:
  provider: fastembed
  model: AllMiniLML6V2
```

Agents start discovery with `memora probe` and use `memora context` or `memora lookup-source` when richer context or source evidence is needed:

```bash
memora probe "<query>" --intent memory|wiki|mixed --variant "<alternate>"
memora context "<query>" --intent evidence|mixed --variant "<alternate>"
```

`search`, `probe`, and `context` accept repeated `--variant` values; Memora merges and deduplicates results. `--mode auto` uses hybrid search when available and falls back to text search if semantic initialization fails.

</details>

## Development

```bash
cargo build
cargo test
```

## CLI Reference

See `docs/cli-agent-reference.md`, `memora help`, or `memora help <command>`.
