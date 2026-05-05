# Memora

Memora is being rebuilt as a Rust CLI-first memory vault for coding agents.

The current product contract lives in `.cursor/rust-rewrite-spec.md`. The new implementation starts from a small command surface focused on:

- local YAML/Markdown vault storage;
- Remi aliases plus implicit agent auto-recall policy;
- project/user agent rule installation;
- raw -> source -> memory/wiki capture;
- indexed search over rebuildable local state.
- local semantic search through `fastembed`, with `local-command` and `deterministic` providers available for custom integrations and tests.

The previous Python implementation has been archived under `.legacy/`.

## Development

```bash
cargo build
cargo test
```

If Rust is not installed on the machine, install the stable toolchain first.

## Local Install

```bash
memora setup
memora self install
eval "$(memora self shell-init zsh)"
memora self completions zsh
```

`self install` copies the current binary into `$MEMORA_HOME/bin/memora`. `self update` overwrites that managed binary while preserving the vault.

## Agent Integration

```bash
memora agent integrate --client all --scope project
memora agent status --client all --scope project
memora agent-aliases set Remi Рэми Реми
memora agent update --client all --scope project
```

Agent rules are written only inside a Memora managed block. Cursor gets `.cursor/rules/memora.mdc`; Claude gets `CLAUDE.md`; Codex and generic agents get `AGENTS.md`.

## Semantic Search

New homes default to the local `fastembed` provider:

```yaml
semantic:
  provider: fastembed
  model: AllMiniLML6V2
```

`memora search|probe|context --mode auto` uses hybrid search when the provider is available and falls back to text search for automatic recall if semantic initialization fails. Explicit `--mode vector` and `--mode hybrid` require a working provider.

Use `--include-related` with `search`, `probe`, or `context` to expand direct matches through indexed memory relations. Ranking combines text/vector score with boosts for memory type, review status, confidence, and relation strength.

Environment overrides:

```bash
MEMORA_SEMANTIC_PROVIDER=local-command
MEMORA_SEMANTIC_MODEL=my-model
MEMORA_SEMANTIC_COMMAND='my-embed-command --json'
```
