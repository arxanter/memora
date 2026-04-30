# Sync Model

Agent Memory is local-first. Sync the human-readable Markdown vault and treat
local generated state as disposable.

## What Syncs

Sync these folders/files with Git, iCloud, Dropbox, Syncthing, or another file
sync tool:

- `Memories/**/*.md`
- `Sources/**/*.md`
- `Briefs/**/*.md`
- `Profiles/**/*.md`
- `Synthesis/**/*.md`
- `.agent-memory/config.yaml`
- `.agent-memory/schemas/`

Do not sync local cache state:

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

The repository `.gitignore` includes these recommendations so nested example or
test vaults do not accidentally commit disposable state.

## Rebuild Local State

SQLite and embeddings are rebuildable from Markdown. After syncing a vault on a
new machine, or after resolving sync conflicts, rebuild the local index:

```bash
memory reindex --vault /path/to/vault --clean
```

`--clean` deletes the existing `.agent-memory/index.sqlite` before indexing. It
is the recovery path for stale, corrupted, or out-of-date local indexes.
Embeddings are also cache data; future embedding rebuilds should follow the same
Markdown-source-of-truth rule.

## Conflict Detection

Agent Memory does not auto-resolve synced Markdown conflicts. Use:

```bash
memory conflicts --vault /path/to/vault
memory conflicts --vault /path/to/vault --json
memory doctor --vault /path/to/vault
```

`memory conflicts` detects practical problems that need manual review:

- Git/file-sync conflict markers such as `<<<<<<<`, `=======`, and `>>>>>>>`.
- Duplicate memory IDs across canonical `Memories/**/*.md` files.
- Invalid memory frontmatter that cannot be parsed or validated.

Resolve the Markdown manually, then run `memory conflicts` again. When it passes,
run `memory reindex --clean` to rebuild local state from the resolved Markdown.

## Atomic Writes

Agent Memory writes Markdown by creating a temporary file in the same directory,
flushing it to disk, and replacing the target path with `os.replace`. This keeps
each Markdown file replacement atomic on the local filesystem and avoids partial
files if a process crashes mid-write.

Multi-file lifecycle operations prepare all temporary files before replacement.
Each individual file replacement is atomic, but this is not a distributed
transaction across synced machines.

## Local Locking

Agent Memory uses a lightweight local lock directory under
`.agent-memory/locks/`. Reindexing, lifecycle mutations, recall metadata touches,
and agent/user memory writes use the same vault lock so local processes do not
write Markdown or rebuild the index concurrently.

The lock is intentionally simple and local. It coordinates Agent Memory
processes on one machine; it is not a robust distributed lock for cloud sync.
If a process crashes and leaves a lock behind, inspect `.agent-memory/locks/`,
confirm no Agent Memory process is running, remove the stale lock directory, and
rerun the command.
