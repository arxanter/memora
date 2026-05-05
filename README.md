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

## Semantic Search

New homes default to the local `fastembed` provider:

```yaml
semantic:
  provider: fastembed
  model: AllMiniLML6V2
```

`memora search|probe|context --mode auto` uses hybrid search when the provider is available and falls back to text search for automatic recall if semantic initialization fails. Explicit `--mode vector` and `--mode hybrid` require a working provider.

Environment overrides:

```bash
MEMORA_SEMANTIC_PROVIDER=local-command
MEMORA_SEMANTIC_MODEL=my-model
MEMORA_SEMANTIC_COMMAND='my-embed-command --json'
```
