# Local Install And Service Management

This guide installs Memora on a local machine so you can run `memora`
without activating a virtual environment by hand.

The installer targets macOS, Linux, and WSL2. It keeps the current Python
implementation, but hides the venv behind stable wrapper commands.

Memora requires Python 3.10 or newer. `scripts/install.sh` automatically
searches for `python3.12`, `python3.11`, `python3.10`, then `python3`. If it
only finds Python 3.9, install a newer Python or pass
`--python /path/to/python3.10`.

## What Gets Installed

By default:

```text
~/.local/share/memora/
  venv/
  logs/

~/.local/bin/
  memory
  memora-service
```

The source scripts are:

- `scripts/install.sh`
- `scripts/memora-service.sh`
- `scripts/uninstall.sh`

The vault is not stored in the install directory. You choose the vault path with
`--vault` or `MEMORA_VAULT`.

## Install Options

For a packaged install, prefer `pipx`:

```bash
pipx install "memora"
memora setup ~/MemoryVault
```

For development from this repository, use an editable install:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
memora setup ./memory-vault
```

## One-liner: clone and install (no prior checkout)

`install.sh` runs `pip install -e` and the `memora-service` wrapper points at your
checkout. **Do not delete the clone** after installation, or imports and the
service wrapper will break. For that reason, a path under `/tmp` is only
appropriate if you know it will not be cleared (many systems wipe `/tmp` on
reboot). Prefer a directory under `$HOME` (see below).

The default Git URL matches `Source` in `pyproject.toml`. Override it with
`MEMORA_REPO_URL` if you install from a fork.

### Recommended (persistent checkout)

```bash
MEMORA_REPO_URL="${MEMORA_REPO_URL:-https://github.com/anton-zhedik/memora.git}"
export MEMORA_CHECKOUT="${MEMORA_CHECKOUT:-$HOME/.local/share/memora/checkout}"
git clone --depth 1 "$MEMORA_REPO_URL" "$MEMORA_CHECKOUT"
"$MEMORA_CHECKOUT/scripts/install.sh" --vault "$HOME/MemoryVault"
```

### Checkout under `/tmp`

Uses `mktemp` so each run gets a unique directory. **Keep that directory** for
as long as you use this Memora install.

```bash
MEMORA_REPO_URL="${MEMORA_REPO_URL:-https://github.com/anton-zhedik/memora.git}"
DIR="$(mktemp -d "${TMPDIR:-/tmp}/memora-checkout.XXXXXX")"
git clone --depth 1 "$MEMORA_REPO_URL" "$DIR"
"$DIR/scripts/install.sh" --vault "$HOME/MemoryVault"
printf 'Editable install and memora-service use this tree; do not rm -rf:\n  %s\n' "$DIR"
```

## One Command Local Install

From the repository root:

```bash
./scripts/install.sh --vault ~/MemoryVault
```

This will:

- Create a managed Python venv.
- Install Memora.
- Install wrapper commands in `~/.local/bin`.
- Initialize the vault if `--vault` is provided.
- Print next steps for `memora setup` and generated agent instructions.

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
./scripts/install.sh --install-dir ~/.local/share/memora --bin-dir ~/.local/bin
./scripts/install.sh --with-test
./scripts/install.sh --dry-run
```

Use `--force` when you want to overwrite existing wrapper scripts. Use
`--with-test` when this checkout is also your development environment.

## CLI After Install

```bash
memora setup ~/MemoryVault --dry-run
memora setup ~/MemoryVault
memora agent-rules --format cursor --vault ~/MemoryVault --project memora
memora install-agent-rules --client cursor --project /path/to/repo --dry-run
memora status
memora remember --type decision --text "Use Markdown as durable memory."
memora reindex --clean
memora search "durable memory"
memora brief "project context" --budget 1200
```

The wrapper sets `MEMORA_VAULT` automatically when it was installed with
`--vault`. You can still override it per command:

```bash
MEMORA_VAULT=~/OtherVault memora status
memora status --vault ~/OtherVault
```

Source import and review are explicit CLI workflows:

```bash
memora import-source ./notes.md --extract-file ./notes-extract.md --project memora --json
memora import-url https://example.com/article --dry-run --json
memora import-pdf ./paper.pdf --text-file ./paper.txt --json
memora import-zoom ./meeting-summary.md --project memora --json
memora import-slack ./thread.json --channel "#memora" --json
memora source-inbox scan --path ~/MemoryVault/raw/inbox --ignore-disabled --dry-run --json
memora review
memora review approve mem_20260430_example --reason "verified source"
memora synthesize "project decisions" --project memora --dry-run --json
```

## Service Manager

The service manager is for local maintenance and index freshness. Its loop polls
durable vault files, runs `memora refresh-index` when Markdown/config/schema
inputs changed, periodically runs `memora doctor`, and writes logs.

Install and start:

```bash
memora-service install
memora-service start
memora-service status
```

Restart and logs:

```bash
memora-service restart
memora-service logs
memora-service logs --no-follow
```

Run a one-shot health check:

```bash
memora-service doctor
```

Run the freshness watcher in the foreground:

```bash
memora-service watch
memora-service watch --watch-interval 10 --watch-debounce 1
```

Disable watcher polling in the background service if you only want periodic
doctor checks:

```bash
memora-service install --no-watch
```

Stop or uninstall:

```bash
memora-service stop
memora-service uninstall
```

## macOS launchd

On macOS, `memora-service install` writes:

```text
~/Library/LaunchAgents/com.memora.service.plist
```

The service is loaded with `launchctl` and runs as your user. Logs are written
under:

```text
~/.local/share/memora/logs/
```

## Linux systemd

On Linux, `memora-service install` writes a `systemd --user` unit:

```text
~/.config/systemd/user/memora.service
```

The service is managed with:

```bash
systemctl --user status memora.service
systemctl --user restart memora.service
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
  Memora commands, such as `/mnt/c/Users/you/Documents/MemoryVault`.
- Do not write Windows paths like `C:\Users\you\MemoryVault` into
  `.memora/config.yaml`; the CLI expects POSIX paths when running in WSL.
- User services require WSL systemd support. Without systemd, use manual
  commands such as `memora refresh-index`, `memora doctor`, and `memora reindex`.

## Upgrade

Pull or update the repository, then rerun:

```bash
./scripts/install.sh --vault ~/MemoryVault --force
```

This refreshes the editable install and wrapper scripts. Your vault Markdown is
not modified except for `memora init` preserving existing config.

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
export MEMORA_VAULT=~/MemoryVault
memora status
```

Service starts but reports errors:

```bash
memora-service logs --no-follow
memora doctor
memora conflicts
```

Stale or corrupted local index:

```bash
memora reindex --clean
```

Remember: Markdown is durable state. SQLite, embeddings, logs, and locks are
local generated state.
