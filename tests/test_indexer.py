import sqlite3

from agent_memory.config import load_config
from agent_memory.indexer import keyword_search, reindex_vault, validate_graph
from agent_memory.vault import init_vault


def test_reindex_creates_stage_four_tables_and_populates_fts(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/use-sqlite.md",
        memory_id="mem_20260430_sqlite1",
        memory_type="decision",
        body="Use SQLite FTS5 as the foundation for keyword recall.",
        observations=[
            {
                "category": "decision",
                "text": "SQLite FTS5 backs the first keyword index.",
                "confidence": 0.9,
            }
        ],
    )
    config = load_config(vault)

    result = reindex_vault(config)

    assert result.documents_seen == 1
    assert result.documents_indexed == 1
    assert result.chunks_indexed == 2
    assert result.observations_indexed == 1
    assert result.graph.ok is True

    with sqlite3.connect(config.index_file) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        assert {
            "documents",
            "chunks",
            "memories",
            "observations",
            "links",
            "chunk_fts",
        } <= tables
        fts_count = connection.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0]
        assert fts_count == 2

    matches = keyword_search(config.index_file, "keyword")
    assert len(matches) == 2
    assert {match.document_id for match in matches} == {"mem_20260430_sqlite1"}


def test_reindex_skips_unchanged_documents_by_content_hash(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/cache.md",
        memory_id="mem_20260430_cache1",
        memory_type="fact",
        body="The index is disposable cache data.",
        observations=[{"category": "fact", "text": "The index is disposable cache data."}],
    )
    config = load_config(vault)

    first = reindex_vault(config)
    second = reindex_vault(config)

    assert first.documents_indexed == 1
    assert second.documents_indexed == 0
    assert second.documents_skipped == 1
    assert second.chunks_indexed == 0
    assert second.chunks_skipped == first.chunks_indexed


def test_reindex_updates_changed_documents_and_removes_deleted_documents(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    memory_path = vault / "Memories" / "facts" / "cache.md"
    _write_memory(
        vault,
        "Memories/facts/cache.md",
        memory_id="mem_20260430_cache1",
        memory_type="fact",
        body="The index is disposable cache data.",
        observations=[{"category": "fact", "text": "The index is disposable cache data."}],
    )
    config = load_config(vault)
    reindex_vault(config)

    _write_memory(
        vault,
        "Memories/facts/cache.md",
        memory_id="mem_20260430_cache1",
        memory_type="fact",
        body="The index is a rebuildable SQLite cache.",
        observations=[{"category": "fact", "text": "The index is a rebuildable SQLite cache."}],
    )
    changed = reindex_vault(config)
    memory_path.unlink()
    removed = reindex_vault(config)

    assert changed.documents_indexed == 1
    assert changed.documents_skipped == 0
    assert removed.documents_removed == 1
    with sqlite3.connect(config.index_file) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0] == 0


def test_graph_validation_reports_orphan_relations(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/orphan.md",
        memory_id="mem_20260430_orphan1",
        memory_type="decision",
        body="This memory points at a missing decision.",
        relations=[{"type": "supports", "target": "mem_20260430_missing1"}],
    )
    config = load_config(vault)

    graph = validate_graph(config)
    result = reindex_vault(config)

    assert graph.ok is False
    assert graph.orphan_count == 1
    assert graph.issues[0].from_id == "mem_20260430_orphan1"
    assert graph.issues[0].to_id == "mem_20260430_missing1"
    assert result.graph.orphan_count == 1
    with sqlite3.connect(config.index_file) as connection:
        assert connection.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1


def _write_memory(
    vault,
    relative_path,
    *,
    memory_id,
    memory_type,
    body,
    observations=None,
    relations=None,
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _memory_markdown(
            memory_id=memory_id,
            memory_type=memory_type,
            body=body,
            observations=observations or [],
            relations=relations or [],
        ),
        encoding="utf-8",
    )


def _memory_markdown(*, memory_id, memory_type, body, observations, relations):
    observation_yaml = "\n".join(
        [
            "  - category: {category}\n    text: {text}{confidence}".format(
                category=observation["category"],
                text=observation["text"],
                confidence=(
                    "\n    confidence: {0}".format(observation["confidence"])
                    if "confidence" in observation
                    else ""
                ),
            )
            for observation in observations
        ]
    )
    relation_yaml = "\n".join(
        [
            "  - type: {type}\n    target: {target}{confidence}".format(
                type=relation["type"],
                target=relation["target"],
                confidence=(
                    "\n    confidence: {0}".format(relation["confidence"])
                    if "confidence" in relation
                    else ""
                ),
            )
            for relation in relations
        ]
    )
    relation_block = "relations:\n{0}".format(relation_yaml) if relation_yaml else "relations: []"
    observation_block = (
        "observations:\n{0}".format(observation_yaml) if observation_yaml else "observations: []"
    )
    return """---
schema_version: 1
id: {memory_id}
type: {memory_type}
status: active
created_at: 2026-04-30T12:00:00+02:00
updated_at: 2026-04-30T12:00:00+02:00
{relations}
{observations}
---

{body}
""".format(
        memory_id=memory_id,
        memory_type=memory_type,
        relations=relation_block,
        observations=observation_block,
        body=body,
    )
