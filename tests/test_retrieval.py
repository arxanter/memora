import sqlite3

from agent_memory.config import SemanticConfig, load_config
from agent_memory.embeddings import DeterministicEmbeddingProvider
from agent_memory.indexer import reindex_vault
from agent_memory.retrieval import RetrievalIndexError, SearchFilters, search_memory
from agent_memory.vault import init_vault


def test_search_requires_existing_index(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config = load_config(vault)

    try:
        search_memory(config, "sqlite")
    except RetrievalIndexError as exc:
        assert "memory reindex" in str(exc)
    else:
        raise AssertionError("search should require the SQLite index")


def test_search_returns_ranked_snippet_citation_and_metadata_filters(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/vector-db.md",
        memory_id="mem_20260430_vector_alpha",
        memory_type="decision",
        scope="project",
        project="alpha",
        body="Use SQLite FTS for vector db recall before adding embeddings.",
        confidence=0.9,
    )
    _write_memory(
        vault,
        "Memories/facts/vector-db-beta.md",
        memory_id="mem_20260430_vector_beta",
        memory_type="fact",
        scope="project",
        project="beta",
        body="Vector db experiments belong to the beta project.",
    )
    config = load_config(vault)
    reindex_vault(config)

    response = search_memory(
        config,
        "vector db",
        filters=SearchFilters(project="alpha", memory_type="decision", status="active"),
    )
    payload = response.to_dict()

    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["result_count"] == 1
    result = payload["results"][0]
    assert result["id"] == "mem_20260430_vector_alpha"
    assert result["score"] > 0
    assert "[vector]" in result["snippet"].lower()
    assert result["citation"] == {
        "id": "mem_20260430_vector_alpha",
        "path": "Memories/decisions/vector-db.md",
        "kind": "memory",
    }
    assert result["metadata"]["project"] == "alpha"
    assert result["metadata"]["type"] == "decision"


def test_search_include_related_expands_links_and_marks_related_results(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/retrieval.md",
        memory_id="mem_20260430_retrieval",
        memory_type="decision",
        body="Agent memory retrieval starts with keyword recall.",
        relations=[{"type": "related_to", "target": "mem_20260430_links"}],
    )
    _write_memory(
        vault,
        "Memories/facts/links.md",
        memory_id="mem_20260430_links",
        memory_type="fact",
        body="The links table provides graph neighbor context.",
    )
    config = load_config(vault)
    reindex_vault(config)

    response = search_memory(config, "retrieval", include_related=True, limit=5)
    results = {result.id: result.to_dict() for result in response.results}

    assert "mem_20260430_retrieval" in results
    assert "mem_20260430_links" in results
    related = results["mem_20260430_links"]
    assert related["related"] is True
    assert related["metadata"]["relation"] == "related_to"
    assert related["metadata"]["related_to"] == "mem_20260430_retrieval"
    assert related["score_breakdown"]["graph_neighbor_boost"] > 0


def test_search_scoring_is_deterministic_without_wall_clock(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/scoring.md",
        memory_id="mem_20260430_scoring",
        memory_type="decision",
        body="Keyword recall scoring is deterministic.",
        confidence=0.8,
    )
    config = load_config(vault)
    reindex_vault(config)

    result = search_memory(config, "keyword recall").results[0].to_dict()

    assert result["score_breakdown"] == {
        "fts_score": 10.0,
        "graph_neighbor_boost": 0.0,
        "memory_type_boost": 0.3,
        "status_boost": 0.4,
        "confidence_boost": 0.4,
        "recency_boost": 0.5,
        "rating_boost": 0.0,
        "stale_penalty": 0.0,
        "superseded_penalty": 0.0,
    }
    assert result["score"] == 11.6


def test_search_preserves_fts_only_behavior_when_semantic_disabled(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/database.md",
        memory_id="mem_20260430_database",
        memory_type="decision",
        body="Database indexing uses SQLite FTS.",
    )
    _write_memory(
        vault,
        "Memories/facts/semantic-only.md",
        memory_id="mem_20260430_semantic_only",
        memory_type="fact",
        body="Databases benefit from vector embeddings.",
    )
    config = _semantic_config(load_config(vault))
    reindex_vault(config)

    response = search_memory(config, "database", semantic=False, limit=5)
    payload = response.to_dict()

    assert payload["semantic"]["enabled"] is False
    assert [result["id"] for result in payload["results"]] == ["mem_20260430_database"]
    assert "semantic_score" not in payload["results"][0]["score_breakdown"]


def test_deterministic_embedding_provider_is_stable():
    provider = DeterministicEmbeddingProvider()

    first = provider.embed(["database memories"])[0]
    second = provider.embed(["database memories"])[0]
    different = provider.embed(["banana"])[0]

    assert first == second
    assert first != different
    assert provider.model == "deterministic-test-v1"


def test_semantic_embedding_cache_refreshes_stale_content_hash(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/database-cache.md",
        memory_id="mem_20260430_database_cache",
        memory_type="fact",
        body="Database cache embeddings are rebuildable.",
    )
    config = _semantic_config(load_config(vault))
    reindex_vault(config)
    search_memory(config, "database", semantic=True)

    with sqlite3.connect(config.index_file) as connection:
        connection.execute("UPDATE embeddings SET content_hash = 'stale', vector = '[0.0]'")
        connection.commit()

    search_memory(config, "database", semantic=True)

    with sqlite3.connect(config.index_file) as connection:
        rows = connection.execute(
            """
            SELECT e.content_hash AS embedding_hash, c.content_hash AS chunk_hash, e.vector AS vector
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            """
        ).fetchall()

    assert rows
    assert all(row[0] == row[1] for row in rows)
    assert all(row[2] != "[0.0]" for row in rows)


def test_hybrid_search_merges_keyword_and_vector_candidates(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/database.md",
        memory_id="mem_20260430_database",
        memory_type="decision",
        body="Database indexing uses SQLite FTS.",
    )
    _write_memory(
        vault,
        "Memories/facts/vector-databases.md",
        memory_id="mem_20260430_vector_databases",
        memory_type="fact",
        body="Databases benefit from vector embeddings.",
    )
    config = _semantic_config(load_config(vault))
    reindex_vault(config)

    results = {
        result.id: result.to_dict()
        for result in search_memory(config, "database", semantic=True, limit=5).results
    }

    assert set(results) == {"mem_20260430_database", "mem_20260430_vector_databases"}
    keyword_result = results["mem_20260430_database"]["score_breakdown"]
    vector_result = results["mem_20260430_vector_databases"]["score_breakdown"]
    assert keyword_result["fts_score"] > 0
    assert keyword_result["semantic_score"] > 0
    assert vector_result["fts_score"] == 0
    assert vector_result["semantic_score"] > 0


def _semantic_config(config):
    return config.model_copy(
        update={
            "semantic": SemanticConfig(
                provider="deterministic",
                model="deterministic-test-v1",
            )
        }
    )


def _write_memory(
    vault,
    relative_path,
    *,
    memory_id,
    memory_type,
    body,
    status="active",
    scope="user",
    project=None,
    confidence=None,
    created_at="2026-04-30T12:00:00+02:00",
    updated_at="2026-04-30T12:00:00+02:00",
    relations=None,
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    relation_yaml = "\n".join(
        [
            "  - type: {type}\n    target: {target}".format(
                type=relation["type"],
                target=relation["target"],
            )
            for relation in relations or []
        ]
    )
    relation_block = "relations:\n{0}".format(relation_yaml) if relation_yaml else "relations: []"
    path.write_text(
        """---
schema_version: 1
id: {memory_id}
type: {memory_type}
scope: {scope}
project: {project}
status: {status}
confidence: {confidence}
created_at: {created_at}
updated_at: {updated_at}
valid_from: 2026-04-30
valid_to:
{relations}
observations:
  - category: {memory_type}
    text: {body}
    confidence: {confidence}
---

{body}
""".format(
            memory_id=memory_id,
            memory_type=memory_type,
            scope=scope,
            project=project or "",
            status=status,
            confidence="" if confidence is None else confidence,
            created_at=created_at,
            updated_at=updated_at,
            relations=relation_block,
            body=body,
        ),
        encoding="utf-8",
    )
