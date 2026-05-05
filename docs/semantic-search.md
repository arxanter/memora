# Semantic Search Setup

Semantic search is local-first and enabled for new vaults by default. Keyword FTS
search and deterministic query planning remain the baseline retrieval path, while
local embeddings add semantic candidates and score boosts when the provider is
available.

## Product Position

Local embeddings are retrieval infrastructure, not the agent's reasoning model.
They do not need to come from the same AI session as the user-facing assistant as
long as retrieval stays local, deterministic, traceable, and safely falls back to
keyword search.

The default provider for new vaults is `fastembed` with
`BAAI/bge-small-en-v1.5`. The installer verifies this provider with a tiny
embedding request, which also warms the normal FastEmbed model cache. The model
is not bundled into the Memora wheel. If the provider is not installed, cannot
initialize, or cannot embed on the current machine, `auto` mode degrades to text
search instead of failing agent recall; a normal install or `memora self update`
should fail earlier instead of leaving users with a silent missing provider.

## Provider Surfaces

The code keeps two narrow provider surfaces:

- `EmbeddingProvider` is the library protocol for embedding integrations. Tests
  or embedding-aware callers can inject an implementation into
  `search_memory(..., embedding_provider=...)` and explicitly request `hybrid`
  or `vector` mode.
- `fastembed` is the built-in local provider. It runs ONNX embeddings locally and
  is the default for new vaults.
- `local-command` is a pre-existing generic JSON stdin/stdout protocol. It is
  preserved for compatibility and custom local/session embedding bridges.
- `deterministic` is a test-only fixture provider. It has no external
  dependencies and produces stable vectors, but it is not a quality embedding
  model and should not be used for production retrieval.

The generic command protocol receives JSON on stdin:

```json
{"model":"same-session-model","texts":["first chunk","second chunk"]}
```

It must write either a raw list of vectors or an object with an `embeddings`
field to stdout:

```json
{"embeddings":[[0.1,0.2],[0.3,0.4]]}
```

If a future Cursor/session embedding bridge is available, it can sit behind the
`EmbeddingProvider` protocol or this generic command contract.

## Operation

Default retrieval:

```bash
memora reindex --vault ./memory-vault
memora search "agent memory retrieval" --vault ./memory-vault --mode auto
```

Retrieval modes:

- `auto`: default; uses `hybrid` when a semantic provider is configured and
  available, and `text` otherwise.
- `text`: SQLite FTS plus deterministic query planning.
- `vector`: embedding similarity only; requires a configured or injected
  provider.
- `hybrid`: merges SQLite FTS and embedding similarity candidates; requires a
  configured or injected provider.

The older `--semantic/--no-semantic` switch is still accepted for compatibility:
`--semantic` maps to `hybrid`, and `--no-semantic` maps to `text`.

All modes use deterministic query planning. The original natural-language query
is tried first, then a small fallback list of normalized and safe stopword-dropped
variants is tried only when the original returns no results or too few strong
results.

On vector or hybrid search with a real provider, missing or stale chunk
embeddings are generated lazily. A cached embedding is considered stale when the
stored `content_hash` no longer matches the indexed chunk `content_hash`; stale
vectors are replaced automatically. The vectors are cached in SQLite only as
derived state. Markdown remains the durable source of truth.

JSON search responses include the semantic mode state:

```json
{
  "mode": "text",
  "requested_mode": "auto",
  "semantic": {
    "enabled": false,
    "provider": null,
    "model": null
  },
  "trace": {
    "planned_query_variants": ["What did we decide about vector DB?", "vector database"],
    "semantic": {
      "status": "not_used",
      "enabled": false,
      "provider": null,
      "model": null
    },
    "attempted_searches": []
  }
}
```

Use `--no-semantic` to force keyword-only search for one query even when a
provider is configured.

Use `--semantic` or `--mode hybrid` when you want semantic retrieval to be
required for that query. Use `--mode vector` when you want vector-only retrieval;
it also requires a configured or injected provider.

## Environment Overrides

You can override the narrow semantic config without editing the vault:

```bash
export MEMORA_SEMANTIC_PROVIDER=local-command
export MEMORA_SEMANTIC_MODEL=same-session-model
export MEMORA_SEMANTIC_BATCH_SIZE=32
export MEMORA_SEMANTIC_DIMENSIONS=1536
export MEMORA_SEMANTIC_MIN_SIMILARITY=0.15
```

These overrides are applied when config is loaded. They do not rewrite
`config.yaml`.
