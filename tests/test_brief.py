from pathlib import Path

from agent_memory.brief import brief_memory
from agent_memory.config import load_config
from agent_memory.indexer import estimate_tokens, reindex_vault
from agent_memory.retrieval import SearchFilters
from agent_memory.vault import init_vault


def test_brief_formats_golden_markdown_with_citations(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/fact.md",
        memory_id="mem_20260430_fact",
        memory_type="fact",
        body="Memory brief facts use Obsidian Markdown as the durable source of truth.",
    )
    _write_memory(
        vault,
        "Memories/decisions/decision.md",
        memory_id="mem_20260430_decision",
        memory_type="decision",
        body="Memory brief decisions do not sync SQLite indexes across devices.",
    )
    _write_memory(
        vault,
        "Memories/facts/stale.md",
        memory_id="mem_20260430_stale",
        memory_type="fact",
        status="stale",
        body="Memory brief warning keeps the SQLite-only design out of current facts.",
    )
    _write_memory(
        vault,
        "Memories/tasks/question.md",
        memory_id="mem_20260430_question",
        memory_type="task",
        body="Memory brief open question asks which embeddings provider should be finalized.",
    )
    config = load_config(vault)
    reindex_vault(config)

    response = brief_memory(config, "memory brief", budget=220)
    expected = Path("tests/fixtures/brief/default.md").read_text(encoding="utf-8")

    assert response.markdown == expected
    assert response.used_tokens_estimate == estimate_tokens(expected)
    assert response.used_tokens_estimate <= response.budget
    assert [citation["key"] for citation in response.citations] == ["C1", "C2", "C3", "C4"]
    assert response.to_dict()["sections"]["warnings"][0]["citations"] == ["C3"]


def test_brief_strict_budget_truncates_sections_before_exceeding(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    for index in range(1, 8):
        _write_memory(
            vault,
            f"Memories/facts/fact-{index}.md",
            memory_id=f"mem_20260430_fact_{index}",
            memory_type="fact",
            body=(
                "Memory brief budget compliance keeps deterministic facts concise "
                f"for selected memory number {index}."
            ),
        )
    config = load_config(vault)
    reindex_vault(config)

    response = brief_memory(config, "memory brief budget", budget=60)

    assert response.truncated is True
    assert response.used_tokens_estimate <= 60
    assert estimate_tokens(response.markdown) <= 60


def test_brief_surfaces_graph_warnings_and_conflicts(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Graph brief keeps Markdown memory as the source of truth.",
        relations=[
            {"type": "supersedes", "target": "mem_20260430_old"},
            {"type": "contradicts", "target": "mem_20260430_other"},
        ],
    )
    _write_memory(
        vault,
        "Memories/decisions/old.md",
        memory_id="mem_20260430_old",
        memory_type="decision",
        status="superseded",
        body="Graph brief stores memory only in SQLite.",
    )
    _write_memory(
        vault,
        "Memories/facts/other.md",
        memory_id="mem_20260430_other",
        memory_type="fact",
        body="Graph brief says SQLite memory remains durable.",
    )
    config = load_config(vault)
    reindex_vault(config)

    response = brief_memory(config, "graph brief", budget=220, include_related=True)
    payload = response.to_dict()

    assert "Superseded memory: mem_20260430_old is superseded by mem_20260430_new." in response.markdown
    assert "Conflict detected: mem_20260430_new contradicts mem_20260430_other." in response.markdown
    assert payload["sections"]["warnings"][0]["text"].startswith("Superseded memory:")
    assert any(item["text"].startswith("Conflict detected:") for item in payload["sections"]["open_questions"])
    assert response.used_tokens_estimate <= response.budget


def test_explicit_stale_brief_keeps_stale_memory_out_of_main_sections(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/stale.md",
        memory_id="mem_20260430_stale_only",
        memory_type="fact",
        status="stale",
        body="Stale brief memory should only appear as a warning.",
    )
    config = load_config(vault)
    reindex_vault(config)

    payload = brief_memory(
        config,
        "stale brief memory",
        filters=SearchFilters(status="stale"),
        budget=120,
    ).to_dict()

    assert payload["sections"]["current_relevant_facts"] == []
    assert payload["sections"]["current_decisions"] == []
    assert payload["sections"]["warnings"][0]["text"] == "Stale: Stale brief memory should only appear as a warning."


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
