# Semantic Search Setup

Semantic search is optional and disabled by default. Keyword FTS search continues
to work without any embedding provider.

## Recommended Local Setup

Use a local embedding model for privacy. The index stores vectors in
`.agent-memory/index.sqlite` as rebuildable cache data; the Markdown files remain
the source of truth and embeddings can be deleted and regenerated at any time.

Configure a local command provider in `.agent-memory/config.yaml`:

```yaml
semantic:
  provider: local-command
  model: nomic-embed-text
  command:
    - ./scripts/embed-local
  timeout_seconds: 30
  vector_limit: 100
  keyword_limit: 100
```

The command receives JSON on stdin:

```json
{"model":"nomic-embed-text","texts":["first chunk","second chunk"]}
```

It must write either a raw list of vectors or an object with an `embeddings`
field to stdout:

```json
{"embeddings":[[0.1,0.2],[0.3,0.4]]}
```

This keeps heavy embedding runtimes outside the core package. Examples of local
backends that can sit behind the command are Ollama, llama.cpp embeddings, or a
small Python script in a user-managed virtualenv.

## Test Provider

The built-in deterministic provider is intended for tests and fixtures:

```yaml
semantic:
  provider: deterministic
  model: deterministic-test-v1
```

It has no external dependencies and produces stable vectors, but it is not a
quality embedding model.

## Operation

After enabling a provider, run:

```bash
memory reindex --vault ./memory-vault
memory search "agent memory retrieval" --vault ./memory-vault --semantic
```

On search, missing or stale chunk embeddings are generated lazily. A cached
embedding is considered stale when the stored `content_hash` no longer matches
the indexed chunk `content_hash`; stale vectors are replaced automatically.

Use `--no-semantic` to force keyword-only search for one query even when a
provider is configured.
