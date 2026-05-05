# Memora

Memora is being rebuilt as a Rust CLI-first memory vault for coding agents.

The current product contract lives in `.cursor/rust-rewrite-spec.md`. The new implementation starts from a small command surface focused on:

- local YAML/Markdown vault storage;
- Remi aliases plus implicit agent auto-recall policy;
- project/user agent rule installation;
- raw -> source -> memory/wiki capture;
- indexed search over rebuildable local state.

The previous Python implementation has been archived under `.legacy/`.

## Development

```bash
cargo build
cargo test
```

If Rust is not installed on the machine, install the stable toolchain first.
