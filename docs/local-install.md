# Local Install And Service Management

This guide installs Agent Memory on a local machine so you can run `memory`
without activating a virtual environment by hand.

The installer targets macOS, Linux, and WSL2. It keeps the current Python
implementation, but hides the venv behind stable wrapper commands.

Agent Memory requires Python 3.10 or newer. `scripts/install.sh` automatically
searches for `python3.12`, `python3.11`, `python3.10`, then `python3`. If it
only finds Python 3.9, install a newer Python or pass
`--python /path/to/python3.10`.

## What Gets Installed

By default:

```text
~/.local/share/agent-memory/
  venv/
  logs/

~/.local/bin/
  memory
  agent-memory-service
```

The source scripts are:

- `scripts/install.sh`
- `scripts/agent-memory-service.sh`
- `scripts/uninstall.sh`

The vault is not stored in the install directory. You choose the vault path with
`--vault` or `AGENT_MEMORY_VAULT`.

## Install Options

For a packaged install, prefer `pipx`:

```bash
pipx install "agent-memory"
memory setup ~/MemoryVault
```

For development from this repository, use an editable install:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
memory setup ./memory-vault
```

## One Command Local Install

From the repository root:

```bash
./scripts/install.sh --vault ~/MemoryVault
```

This will:

- Create a managed Python venv.
- Install Agent Memory.
- Install wrapper commands in `~/.local/bin`.
- Initialize the vault if `--vault` is provided.
- Print next steps for `memory setup` and generated agent instructions.

If needed, add wrappers to your shell path:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Installer Options

```bash
./scripts/install.sh --help
```

Common options:

```bash
./scripts/install.sh --vault ~/MemoryVault --force
./scripts/install.sh --vault ~/MemoryVault --python /opt/homebrew/bin/python3.12
./scripts/install.sh --install-dir ~/.local/share/agent-memory --bin-dir ~/.local/bin
./scripts/install.sh --with-test
./scripts/install.sh --dry-run
```

Use `--force` when you want to overwrite existing wrapper scripts. Use
`--with-test` when this checkout is also your development environment.

## CLI After Install

```bash
memory setup ~/MemoryVault --dry-run
memory setup ~/MemoryVault
memory agent-rules --format cursor --vault ~/MemoryVault --project agent-memory
memory install-agent-rules --client cursor --project /path/to/repo --dry-run
memory status
memory remember --type decision --text "Use Markdown as durable memory."
memory reindex --clean
memory search "durable memory"
memory brief "project context" --budget 1200
```

The wrapper sets `AGENT_MEMORY_VAULT` automatically when it was installed with
`--vault`. You can still override it per command:

```bash
AGENT_MEMORY_VAULT=~/OtherVault memory status
memory status --vault ~/OtherVault
```

Source import and review are explicit CLI workflows:

```bash
memory import-source ./notes.md --extract-file ./notes-extract.md --project agent-memory --json
memory import-url https://example.com/article --dry-run --json
memory import-pdf ./paper.pdf --text-file ./paper.txt --json
memory import-zoom ./meeting-summary.md --project agent-memory --json
memory import-slack ./thread.json --channel "#agent-memory" --json
memory source-inbox scan --path ~/MemoryVault/raw/inbox --ignore-disabled --dry-run --json
memory review
memory review approve mem_20260430_example --reason "verified source"
memory synthesize "project decisions" --project agent-memory --dry-run --json
```

## Service Manager

The service manager is for local maintenance and index freshness. Its loop polls
durable vault files, runs `memory refresh-index` when Markdown/config/schema
inputs changed, periodically runs `memory doctor`, and writes logs.

Install and start:

```bash
agent-memory-service install
agent-memory-service start
agent-memory-service status
```

Restart and logs:

```bash
agent-memory-service restart
agent-memory-service logs
agent-memory-service logs --no-follow
```

Run a one-shot health check:

```bash
agent-memory-service doctor
```

Run the freshness watcher in the foreground:

```bash
agent-memory-service watch
agent-memory-service watch --watch-interval 10 --watch-debounce 1
```

Disable watcher polling in the background service if you only want periodic
doctor checks:

```bash
agent-memory-service install --no-watch
```

Stop or uninstall:

```bash
agent-memory-service stop
agent-memory-service uninstall
```

## macOS launchd

On macOS, `agent-memory-service install` writes:

```text
~/Library/LaunchAgents/com.agent-memory.service.plist
```

The service is loaded with `launchctl` and runs as your user. Logs are written
under:

```text
~/.local/share/agent-memory/logs/
```

## Linux systemd

On Linux, `agent-memory-service install` writes a `systemd --user` unit:

```text
~/.config/systemd/user/agent-memory.service
```

The service is managed with:

```bash
systemctl --user status agent-memory.service
systemctl --user restart agent-memory.service
```

If user services do not start after login, enable lingering if appropriate for
your machine:

```bash
loginctl enable-linger "$USER"
```

## Windows And WSL

Native Windows installation is not first-class yet. Use WSL2 with Ubuntu or
another Linux distribution, install Python 3.10 or newer there, and run `pipx`,
the editable install, or `./scripts/install.sh` inside WSL.

Path guidance:

- Prefer `~/MemoryVault` inside WSL for the best CLI, service, and file watching
  behavior.
- If Obsidian on Windows must open the same vault, use the WSL path form in
  Agent Memory commands, such as `/mnt/c/Users/you/Documents/MemoryVault`.
- Do not write Windows paths like `C:\Users\you\MemoryVault` into
  `.agent-memory/config.yaml`; the CLI expects POSIX paths when running in WSL.
- User services require WSL systemd support. Without systemd, use manual
  commands such as `memory refresh-index`, `memory doctor`, and `memory reindex`.

## Upgrade

Pull or update the repository, then rerun:

```bash
./scripts/install.sh --vault ~/MemoryVault --force
```

This refreshes the editable install and wrapper scripts. Your vault Markdown is
not modified except for `memory init` preserving existing config.

## Uninstall

Remove wrappers and service files:

```bash
./scripts/uninstall.sh
```

Optionally remove managed venv and logs:

```bash
./scripts/uninstall.sh --remove-venv --remove-logs
```

Uninstall does not delete your vault Markdown.

## Troubleshooting

Wrapper command not found:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

No vault configured:

```bash
export AGENT_MEMORY_VAULT=~/MemoryVault
memory status
```

Service starts but reports errors:

```bash
agent-memory-service logs --no-follow
memory doctor
memory conflicts
```

Stale or corrupted local index:

```bash
memory reindex --clean
```

Remember: Markdown is durable state. SQLite, embeddings, logs, and locks are
local generated state.
