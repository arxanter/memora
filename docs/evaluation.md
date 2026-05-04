# Evaluation Plan

## Goals

Evaluation should prove that the system gives coding agents useful, compact, and trustworthy context from local Markdown memory.

The first evaluation focus is deterministic behavior:

- Correct memories are recalled.
- Irrelevant memories are excluded.
- Lifecycle states affect output correctly.
- Every returned item has a citation.
- Token budgets are respected.
- SQLite can be rebuilt from Markdown with equivalent results.

## Stage 12 Evaluation Set

Stage 12 adds an executable deterministic evaluation set at
`tests/fixtures/evaluation/coding-agent-questions.yaml`. It currently contains
30-50 representative coding-agent questions over `tests/fixtures/vault-basic`.
Each case includes:

- Query text.
- Project or scope filters, when relevant.
- Expected included memory IDs.
- Expected excluded memory IDs.
- Expected warning or conflict behavior.
- Maximum token budget.
- Expected citation paths.
- Optional explainability expectations that assert returned chunks/results expose
  citations and score metadata.

Example categories:

- "What did we decide about this architecture?"
- "What are my preferences for this project?"
- "Which earlier decision does this replace?"
- "Build context for this coding task."
- "What stale or contradicted guidance might affect this?"

## Core Metrics

Recall quality:

- Required memories included.
- Known irrelevant memories excluded.
- Superseded and rejected memories hidden by default.
- Stale memories shown only as warnings when relevant.

Context packing:

- Output never exceeds requested token budget.
- High-value active memories are prioritized.
- Duplicate or near-duplicate chunks are not packed repeatedly.
- Citations exist for every packed chunk.

Brief quality:

- Stable sections are present.
- Current facts and decisions are separated from warnings.
- Open questions and contradictions are surfaced when known.
- Citations remain attached to claims.

Rebuildability:

- `memora reindex` recreates the expected SQLite cache from Markdown.
- Content hash changes trigger reindexing.
- Deleting generated cache data does not lose durable memory.

Compatibility:

- Generic Markdown can be imported as source material.
- Basic Memory-like observations and relations can be imported or exported where feasible.
- `CLAUDE.md`, `AGENTS.md`, and Cursor rules can be read as source inputs without becoming canonical memory automatically.

## Fixture Vaults

Stage 12 fixture vaults:

```text
tests/fixtures/vault-basic
tests/fixtures/vault-conflicts
tests/fixtures/vault-large
tests/fixtures/vault-basic-memory-import
```

Each fixture is small enough to review manually and rich enough to cover
lifecycle state, relations, citations, project scoping, sync conflicts, rebuilds,
and Basic Memory import/export compatibility shape.

## Running Evaluation

Use the lightweight Python harness from tests:

```python
from evaluation import run_evaluation

report = run_evaluation("tests/fixtures/evaluation/coding-agent-questions.yaml")
```

The harness copies the fixture vault to a temporary directory, runs a clean
reindex, then evaluates search, recall, brief, review, conflict, or doctor cases
against expected IDs, warning text, token budget, and explainability metadata.
This keeps repository fixtures immutable while still testing rebuild behavior.

Import/export placeholders are not part of the CLI. The
`vault-basic-memory-import` fixture remains as data for future importer design,
but scripts should use explicit raw, source, memory, and recall commands.

## Stage 0 Acceptance

Stage 0 does not require executable tests. It requires enough evaluation design to guide implementation of schema fixtures, retrieval tests, token-budget tests, lifecycle tests, and Basic Memory compatibility tests in later stages.
