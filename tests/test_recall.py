from agent_memory.config import RecallConfig, load_config
from agent_memory.indexer import estimate_tokens, reindex_vault
from agent_memory.recall import RecallCandidate, pack_candidates, pack_candidates_with_trace, recall_memory
from agent_memory.retrieval import SearchFilters
from agent_memory.vault import init_vault


def test_pack_candidates_is_deterministic_and_applies_diversity_caps():
    candidates = [
        _candidate("doc-a:chunk:1", "doc-a", "Alpha project token packing body", score=9.0, project="alpha"),
        _candidate("doc-a:chunk:2", "doc-a", "Alpha project token packing observation", score=8.0, project="alpha"),
        _candidate("doc-b:chunk:1", "doc-b", "Beta project token packing body", score=7.0, project="beta"),
    ]
    config = RecallConfig(max_chunks_per_document=1, max_chunks_per_project=1)

    first = pack_candidates(candidates, budget=80, recall_config=config)
    second = pack_candidates(list(reversed(candidates)), budget=80, recall_config=config)

    assert [chunk.chunk_id for chunk in first] == ["doc-a:chunk:1", "doc-b:chunk:1"]
    assert [chunk.to_dict() for chunk in first] == [chunk.to_dict() for chunk in second]


def test_pack_candidates_truncates_oversized_chunks_and_never_exceeds_budget():
    text = " ".join(f"token{i}" for i in range(100))
    candidates = [_candidate("doc-a:chunk:1", "doc-a", text, score=10.0)]

    packed = pack_candidates(
        candidates,
        budget=15,
        recall_config=RecallConfig(max_tokens_per_chunk=50),
    )

    assert len(packed) == 1
    assert packed[0].truncated is True
    assert packed[0].token_estimate <= 15
    assert sum(chunk.token_estimate for chunk in packed) <= 15


def test_pack_candidates_dedupes_near_identical_chunks():
    candidates = [
        _candidate("doc-a:chunk:1", "doc-a", "Use SQLite FTS for memory recall.", score=10.0),
        _candidate("doc-b:chunk:1", "doc-b", "Use sqlite fts for memory recall!", score=9.0),
        _candidate("doc-c:chunk:1", "doc-c", "Use token budgets for packed context.", score=8.0),
    ]

    packed = pack_candidates(candidates, budget=80)

    assert [chunk.document_id for chunk in packed] == ["doc-a", "doc-c"]


def test_pack_candidates_with_trace_reports_skip_reasons():
    candidates = [
        _candidate("doc-a:chunk:1", "doc-a", "Trace packing duplicate text.", score=10.0),
        _candidate("doc-b:chunk:1", "doc-b", "Trace packing duplicate text.", score=9.0),
        _candidate("doc-c:chunk:1", "doc-c", "Trace packing project cap text.", score=8.0, project="alpha"),
        _candidate("doc-d:chunk:1", "doc-d", "Trace packing project cap other.", score=7.0, project="alpha"),
    ]

    result = pack_candidates_with_trace(
        candidates,
        budget=80,
        recall_config=RecallConfig(max_chunks_per_project=1),
    )

    assert [chunk.document_id for chunk in result.chunks] == ["doc-a", "doc-c"]
    skipped = {(item.candidate.document_id, item.reason) for item in result.trace if item.action == "skipped"}
    assert ("doc-b", "duplicate") in skipped
    assert ("doc-d", "cap_filtered") in skipped


def test_recall_memory_respects_lifecycle_defaults_and_status_filters(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/old.md",
        memory_id="mem_20260430_old",
        memory_type="fact",
        body="Budget packing should not return superseded old memory.",
    )
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Budget packing should return the new replacement memory.",
        relations=[{"type": "supersedes", "target": "mem_20260430_old"}],
    )
    _write_memory(
        vault,
        "Memories/facts/pending.md",
        memory_id="mem_20260430_pending",
        memory_type="fact",
        status="pending",
        body="Budget packing pending memory needs explicit status.",
    )
    _write_memory(
        vault,
        "Memories/facts/rejected.md",
        memory_id="mem_20260430_rejected",
        memory_type="fact",
        status="rejected",
        body="Budget packing rejected memory needs explicit status.",
    )
    _write_memory(
        vault,
        "Memories/facts/stale.md",
        memory_id="mem_20260430_stale",
        memory_type="fact",
        status="stale",
        body="Budget packing stale memory remains available by default.",
    )
    config = load_config(vault)
    reindex_vault(config)

    default_payload = recall_memory(config, "budget packing", budget=80).to_dict()
    default_ids = {chunk["id"] for chunk in default_payload["chunks"]}

    assert "mem_20260430_new" in default_ids
    assert "mem_20260430_stale" in default_ids
    assert "mem_20260430_old" not in default_ids
    assert "mem_20260430_pending" not in default_ids
    assert "mem_20260430_rejected" not in default_ids
    assert default_payload["used_tokens_estimate"] <= default_payload["budget"]
    assert all(chunk["citation"] in default_payload["citations"] for chunk in default_payload["chunks"])

    pending_payload = recall_memory(
        config,
        "budget packing",
        filters=SearchFilters(status="pending"),
        budget=80,
    ).to_dict()

    assert [chunk["id"] for chunk in pending_payload["chunks"]] == ["mem_20260430_pending"]


def test_recall_memory_never_exceeds_tiny_budget(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/long.md",
        memory_id="mem_20260430_long",
        memory_type="decision",
        body=" ".join(["budget"] * 200),
    )
    config = load_config(vault)
    reindex_vault(config)

    payload = recall_memory(config, "budget", budget=5).to_dict()

    assert payload["chunk_count"] == 1
    assert payload["used_tokens_estimate"] <= 5
    assert payload["chunks"][0]["truncated"] is True


def test_recall_memory_omits_loaded_memory_ids_and_reports_session(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/session-loaded.md",
        memory_id="mem_20260502_session_loaded",
        memory_type="decision",
        body="Session dedupe should not return this loaded memory again.",
    )
    _write_memory(
        vault,
        "Memories/decisions/session-remaining.md",
        memory_id="mem_20260502_session_remaining",
        memory_type="decision",
        body="Session dedupe should return this remaining memory instead.",
    )
    config = load_config(vault)
    reindex_vault(config)

    baseline = recall_memory(config, "session dedupe", budget=120).to_dict()
    payload = recall_memory(
        config,
        "session dedupe",
        budget=120,
        session_id="agent-session-1",
        loaded_memory_ids=("mem_20260502_session_loaded",),
    ).to_dict()

    assert "session" not in baseline
    assert "mem_20260502_session_loaded" in {chunk["id"] for chunk in baseline["chunks"]}
    assert "mem_20260502_session_loaded" not in {chunk["id"] for chunk in payload["chunks"]}
    assert "mem_20260502_session_remaining" in {chunk["id"] for chunk in payload["chunks"]}
    assert payload["session"]["session_id"] == "agent-session-1"
    assert payload["session"]["loaded_memory_ids"] == ["mem_20260502_session_loaded"]
    assert payload["session"]["filtered_memory_ids"] == ["mem_20260502_session_loaded"]
    assert payload["session"]["filtered_memory_count"] == 1


def test_recall_memory_omits_loaded_source_ids(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/source-loaded.md",
        memory_id="mem_20260502_source_loaded",
        memory_type="fact",
        body="Source dedupe should skip memory backed by an already loaded source.",
        source_path="Sources/2026-05-02_loaded/extract.md",
    )
    _write_memory(
        vault,
        "Memories/facts/source-remaining.md",
        memory_id="mem_20260502_source_remaining",
        memory_type="fact",
        body="Source dedupe should keep memory backed by a different source.",
        source_path="Sources/2026-05-02_remaining/extract.md",
    )
    config = load_config(vault)
    reindex_vault(config)

    payload = recall_memory(
        config,
        "source dedupe",
        budget=120,
        session_id="agent-session-2",
        loaded_source_ids=("2026-05-02_loaded",),
    ).to_dict()

    assert "mem_20260502_source_loaded" not in {chunk["id"] for chunk in payload["chunks"]}
    assert "mem_20260502_source_remaining" in {chunk["id"] for chunk in payload["chunks"]}
    assert payload["session"]["loaded_source_ids"] == ["2026-05-02_loaded"]
    assert payload["session"]["filtered_memory_ids"] == ["mem_20260502_source_loaded"]
    assert payload["session"]["filtered_source_ids"] == ["2026-05-02_loaded"]


def _candidate(
    chunk_id,
    document_id,
    text,
    *,
    score,
    project=None,
    memory_type="decision",
):
    return RecallCandidate(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_type="body",
        text=text,
        path=f"Memories/decisions/{document_id}.md",
        memory_type=memory_type,
        status="active",
        scope="project" if project else "user",
        project=project,
        score=score,
        token_estimate=estimate_tokens(text),
        content_hash=chunk_id,
        metadata={
            "type": memory_type,
            "status": "active",
            "scope": "project" if project else "user",
            "project": project,
            "chunk_id": chunk_id,
            "chunk_type": "body",
        },
        score_breakdown={"fts_score": score},
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
    source_path=None,
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
    source_block = "source:\n  path: {0}\n".format(source_path) if source_path else "source:\n"
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
{source_block}
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
            source_block=source_block,
            relations=relation_block,
            body=body,
        ),
        encoding="utf-8",
    )
