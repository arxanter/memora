# Semantic Search Setup

Semantic search is optional and disabled by default. Keyword FTS search and
deterministic query planning continue to work without embeddings and are the safe
default for the standalone CLI.

## Current Constraint

Production embeddings must come from the same AI model/session that the user is
interacting with. The standalone `memory` CLI does not currently have technical
access to Cursor's active AI session embeddings, so it must not claim that
same-session semantic search is available.

Agent Memory therefore does not include first-class OpenAI, Ollama, FastEmbed, or
other public/open/local model providers. Under this constraint, normal production
retrieval should use `auto` mode with no semantic provider configured; it falls
back to text search plus query planning.

## Provider Surfaces

The code keeps two narrow provider surfaces:

- `EmbeddingProvider` is the library protocol for future same-session embedding
  integrations. Tests or embedding-aware callers can inject an implementation
  into `search_memory(..., embedding_provider=...)` and explicitly request
  `hybrid` or `vector` mode.
- `local-command` is a pre-existing generic JSON stdin/stdout protocol. It is
  preserved for compatibility, but it is not the recommended production path
  under the same-session constraint.
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
`EmbeddingProvider` protocol or this generic command contract. Until then, do
not configure a provider just to get semantic search.

## Operation

Default retrieval:

```bash
memory reindex --vault ./memory-vault
memory search "agent memory retrieval" --vault ./memory-vault --mode auto
```

Retrieval modes:

- `auto`: default; uses `hybrid` only when a semantic provider is explicitly
  configured, and `text` otherwise.
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

Use `--semantic` or `--mode hybrid` only when a provider that satisfies the
same-session constraint is explicitly available. Use `--mode vector` when you
want vector-only retrieval; it also requires a configured or injected provider.

## Environment Overrides

You can override the narrow semantic config without editing the vault:

```bash
export AGENT_MEMORY_SEMANTIC_PROVIDER=local-command
export AGENT_MEMORY_SEMANTIC_MODEL=same-session-model
export AGENT_MEMORY_SEMANTIC_BATCH_SIZE=32
export AGENT_MEMORY_SEMANTIC_DIMENSIONS=1536
export AGENT_MEMORY_SEMANTIC_MIN_SIMILARITY=0.15
```

These overrides are applied when config is loaded. They do not rewrite
`.agent-memory/config.yaml`. Do not use them to point Agent Memory at public/open
or local-model providers when the same-session constraint applies.
