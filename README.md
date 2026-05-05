# Memora

Memora is a tiny CLI-first memory vault for coding agents. It stores durable
facts, decisions, preferences, tasks, curated source evidence, and maintained
Wiki pages as Markdown, then lets agents retrieve compact context through the
`memora` CLI.

Use it when you want Cursor, Claude, Codex, or another agent to remember project
decisions without editing memory files directly.

## Install

Requirements: macOS or Linux, Python 3.10 or newer, and `git`.

```bash
git clone https://github.com/arxanter/memora.git /tmp/memora-installer
cd /tmp/memora-installer
./scripts/install.sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer creates a managed `~/memora` directory:

```text
~/memora/
  engine/      Memora git checkout used by self update
  vault/       durable Markdown data
  config.yaml  user configuration
  state/       rebuildable indexes, caches, embeddings, and locks
  venv/        managed Python environment
```

Check that it works:

```bash
memora status
```

The wrapper stores `MEMORA_HOME`, so normal commands do not need `--vault`.

To softly update the Memora source checkout without deleting local files:

```bash
memora self update --remote-url https://github.com/arxanter/memora.git
```

This stashes local changes in `~/memora/engine`, pulls fast-forward updates, and
reapplies the saved stash. By default it also reruns the installer with
`--force`, so `venv/` and the wrapper pick up new runtime dependencies. It never
removes or rewrites `vault/`.

Advanced install options

```bash
./scripts/install.sh --help
./scripts/install.sh --home ~/memora --with-test
```

Common options to add when needed: `--install-dir <path>`, `--bin-dir <path>`,
`--python <path>`, `--dry-run`, `--force`.

The installer creates a managed virtual environment, ensures `engine/` exists,
installs Memora from that checkout, writes the `memora` wrapper, and initializes
the managed home. When `uv` is available, the installer uses it for faster
environment creation and package installation; otherwise it falls back to Python
`venv` and `pip`.

On Windows, use WSL2 and run the same commands inside Linux.



## Developer Tooling

Memora uses `uv` for dependency management, environment synchronization, linting,
and test execution:

```bash
uv sync --group test --group lint
uv run --group lint ruff check .
uv run --group lint ruff format --check .
uv run --group test pytest
```

## Connect An Agent

Install generated memory instructions into a project:

```bash
memora agent integrate --client all --project /path/to/project
memora agent integrate --client all --project .
```

After that, the agent uses Memora itself. Address the assistant as `Remi`,
`Рэми`, or `Реми` when you want memory work, for example:

- “Remi, what did we decide about storage?”
- “Рэми, сохрани это как решение.”
- “Remi, review pending memory.”

## How It Works

The agent follows one layered flow:

```text
raw input -> curated source -> atomic memory + Wiki -> recalled context
```

Humans usually do not need to run the lower-level memory commands directly.
Generated agent rules use compact CLI output and preserve citations.

What the agent does behind the scenes

- Retrieves memory only when it is relevant to the user request.
- Saves durable facts, decisions, preferences, tasks, and project context with
small atomic memory entries.
- Stages raw files separately from curated source evidence.
- Maintains Wiki pages for overviews, entities, concepts, source summaries, and
  saved syntheses.
- Moves successfully processed raw files out of `raw/inbox` with
  `memora raw mark-processed`.
- Leaves inferred agent-authored memories pending for review unless policy says
otherwise.
- Avoids storing secrets, raw logs, and temporary implementation chatter as
canonical memory.
- Treats `memora brief` as ephemeral output; durable saved briefs and analyses
  go into `Wiki/syntheses/`.



## Uninstall

From the cloned repository:

```bash
cd ~/memora/engine
./scripts/uninstall.sh --remove-venv
```

This removes local wrapper commands and the managed virtual environment. Your
Markdown vault is not deleted.

Remove everything except the vault

```bash
cd ~/memora/engine
./scripts/uninstall.sh --remove-venv
rm -rf ~/memora/engine
```

To preview removal:

```bash
./scripts/uninstall.sh --remove-venv --dry-run
```



## More

- CLI command reference for agents: `docs/cli-agent-reference.md`
- Technical architecture: `docs/architecture.md`

