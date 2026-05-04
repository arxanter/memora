# Memora

Memora is a tiny CLI-first memory vault for coding agents. It stores durable
facts, decisions, preferences, tasks, and curated source notes as Markdown, then
lets agents retrieve compact context through the `memora` CLI.

Use it when you want Cursor, Claude, Codex, or another agent to remember project
decisions without editing memory files directly.

## Install

Requirements: macOS or Linux, Python 3.10 or newer, `git`.

```bash
git clone https://github.com/arxanter/memora.git ~/.local/src/memora
cd ~/.local/src/memora
./scripts/install.sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer asks where to create/use the default vault. Press Enter to use
`~/MemoryVault`, enter another path, or type `skip` to configure it later.

Check that it works:

```bash
memora status --json
```

The installer stores your selected default vault in the `memora` wrapper, so
normal commands do not need `--vault`.

To move the default later, initialize the new vault and point the wrapper at it:

```bash
memora init ~/NewMemoryVault --set-default
# or, for an existing initialized vault:
memora vault set ~/ExistingMemoryVault
```

To force-refresh the Memora source checkout and reinstall the local wrapper:

```bash
rm -rf ~/.local/src/memora
git clone https://github.com/arxanter/memora.git ~/.local/src/memora
cd ~/.local/src/memora
./scripts/install.sh --force --no-vault
```

This replaces only the source checkout and wrapper install. It does not remove
or modify your vault.

<details>
<summary>Advanced install options</summary>

```bash
./scripts/install.sh --help
./scripts/install.sh --vault ~/MemoryVault --with-test
./scripts/install.sh --no-vault
```

Common options to add when needed: `--install-dir <path>`, `--bin-dir <path>`,
`--python <path>`, `--no-vault`, `--dry-run`, `--force`.

The installer creates a managed virtual environment, installs Memora from the
cloned repository, writes the `memora` wrapper, and initializes the vault when
`--vault` is provided.

On Windows, use WSL2 and run the same commands inside Linux.

</details>

## Connect An Agent

Install generated memory instructions into a project:

```bash
memora agent integrate --client all --project /path/to/project --json
```

After that, the agent uses Memora itself. Address the assistant as `Remi`,
`Рэми`, or `Реми` when you want memory work, for example:

- “Remi, what did we decide about storage?”
- “Рэми, сохрани это как решение.”
- “Remi, review pending memory.”

## How It Works

The agent follows one simple flow:

```text
raw input -> curated source -> atomic memory -> recalled context
```

Humans usually do not need to run the lower-level memory commands directly.
Generated agent rules call them with `--json` and preserve citations.

<details>
<summary>What the agent does behind the scenes</summary>

- Retrieves memory only when it is relevant to the user request.
- Saves durable facts, decisions, preferences, tasks, and project context with
  small atomic memory entries.
- Stages raw files separately from curated source evidence.
- Leaves inferred agent-authored memories pending for review unless policy says
  otherwise.
- Avoids storing secrets, raw logs, and temporary implementation chatter as
  canonical memory.

</details>

## Uninstall

From the cloned repository:

```bash
cd ~/.local/src/memora
./scripts/uninstall.sh --remove-venv
```

This removes local wrapper commands and the managed virtual environment. Your
Markdown vault is not deleted.

<details>
<summary>Remove everything except the vault</summary>

```bash
cd ~/.local/src/memora
./scripts/uninstall.sh --remove-venv
rm -rf ~/.local/src/memora
```

To preview removal:

```bash
./scripts/uninstall.sh --remove-venv --dry-run
```

</details>

## More

- CLI command reference for agents: `docs/cli-agent-reference.md`
- Technical architecture: `docs/architecture.md`
