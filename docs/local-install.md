# Local Install And Service Management

This guide installs Agent Memory on a local machine so you can run `memory`,
`memory-mcp`, and service-management commands without activating a virtual
environment by hand.

The installer targets macOS and Linux. It keeps the current Python
implementation, but hides the venv behind stable wrapper commands.

The installer installs the MCP extra by default. The upstream `mcp` package
requires Python 3.10 or newer, so `scripts/install.sh` automatically searches for
`python3.12`, `python3.11`, `python3.10`, then `python3`. If it only finds
Python 3.9, install a newer Python or pass `--python /path/to/python3.10`.

## What Gets Installed

By default:

```text
~/.local/share/agent-memory/
  venv/
  logs/

~/.local/bin/
  memory
  memory-mcp
  agent-memory-service
```

The source scripts are:

- `scripts/install.sh`
- `scripts/agent-memory-service.sh`
- `scripts/uninstall.sh`

The vault is not stored in the install directory. You choose the vault path with
`--vault` or `AGENT_MEMORY_VAULT`.

## One Command Install

From the repository root:

```bash
./scripts/install.sh --vault ~/MemoryVault
```

This will:

- Create a managed Python venv.
- Install Agent Memory with the MCP extra.
- Install wrapper commands in `~/.local/bin`.
- Initialize the vault if `--vault` is provided.
- Print an MCP config snippet for Claude Code, Cursor, Codex, or another MCP
  client.

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

## MCP Activation

Agent Memory currently exposes a stdio MCP server. MCP clients such as Claude
Code and Cursor should launch `memory-mcp` on demand. Do not run `memory-mcp` as
a background daemon unless the client explicitly asks you to provide a stdio
process.

Example client config:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "/Users/you/.local/bin/memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

If the client cannot find the wrapper, use the absolute path printed by
`./scripts/install.sh`.

You can print the config again at any time:

```bash
memory mcp-config
memory mcp-config --format claude
memory mcp-config --format cursor
```

## Service Manager

The service manager is for local maintenance and index freshness. It is not the
stdio MCP server. Its loop polls durable vault files, runs `memory refresh-index`
when Markdown/config/schema inputs changed, periodically runs `memory doctor`,
and writes logs.

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

MCP client cannot start the server:

- Use an absolute `command` path such as `/Users/you/.local/bin/memory-mcp`.
- Confirm the vault path in the MCP config is absolute.
- Run `memory-mcp` from a terminal to confirm imports work.

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
