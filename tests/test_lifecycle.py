import json

from typer.testing import CliRunner

from agent_memory.cli import app
from agent_memory.config import load_config
from agent_memory.indexer import reindex_vault
from agent_memory.lifecycle import (
    contradict_memories,
    decay_memories,
    reject_memory,
    review_queue,
    supersede_memory,
)
from agent_memory.retrieval import SearchFilters, search_memory
from agent_memory.schema import validate_markdown_file
from agent_memory.vault import doctor_report, init_vault


runner = CliRunner()


def test_mark_reject_and_review_lifecycle_commands(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/agent.md",
        memory_id="mem_20260430_agent",
        memory_type="fact",
        status="pending",
        body="Lifecycle review keeps agent memory pending.",
        author_kind="agent",
        source_path="Sources/2026-04-30_lifecycle/source.md",
        confidence=0.7,
    )

    review_payload = runner.invoke(app, ["review", "--vault", str(vault), "--json"])
    mark_payload = runner.invoke(
        app,
        ["mark", "mem_20260430_agent", "--status", "stale", "--vault", str(vault), "--json"],
    )
    reject_payload = runner.invoke(app, ["reject", "mem_20260430_agent", "--vault", str(vault), "--json"])

    assert review_payload.exit_code == 0, review_payload.output
    assert mark_payload.exit_code == 0, mark_payload.output
    assert reject_payload.exit_code == 0, reject_payload.output
    assert json.loads(review_payload.output)["items"][0]["id"] == "mem_20260430_agent"
    assert json.loads(mark_payload.output)["mutations"][0]["status"] == "stale"
    assert json.loads(reject_payload.output)["mutations"][0]["status"] == "rejected"

    document = validate_markdown_file(vault / "Memories/facts/agent.md")
    assert document.frontmatter.status == "rejected"
    assert document.frontmatter.valid_to is not None
    history = document.frontmatter.model_dump(mode="json")["history"]
    assert [entry["action"] for entry in history] == ["mark_status", "reject"]


def test_supersede_updates_status_relation_and_audit_together(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/old.md",
        memory_id="mem_20260430_old",
        memory_type="decision",
        body="Use old lifecycle behavior.",
    )
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Use new lifecycle behavior.",
    )
    config = load_config(vault)

    result = supersede_memory(config, "mem_20260430_old", new_id="mem_20260430_new")

    assert result.to_dict()["relation"] == "supersedes"
    old_doc = validate_markdown_file(vault / "Memories/decisions/old.md")
    new_doc = validate_markdown_file(vault / "Memories/decisions/new.md")
    assert old_doc.frontmatter.status == "superseded"
    assert old_doc.frontmatter.valid_to is not None
    assert "mem_20260430_old" in new_doc.frontmatter.supersedes
    assert old_doc.frontmatter.model_dump(mode="json")["history"][0]["by"] == "mem_20260430_new"
    assert new_doc.frontmatter.model_dump(mode="json")["history"][0]["target"] == "mem_20260430_old"

    reindex_vault(config)
    rows = search_memory(
        config,
        "lifecycle behavior",
        filters=SearchFilters(status="superseded"),
    ).to_dict()["results"]
    assert [row["id"] for row in rows] == ["mem_20260430_old"]


def test_contradict_surfaces_in_brief_and_doctor_without_missing_link(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/left.md",
        memory_id="mem_20260430_left",
        memory_type="decision",
        body="Contradiction lifecycle says Markdown is durable.",
    )
    _write_memory(
        vault,
        "Memories/facts/right.md",
        memory_id="mem_20260430_right",
        memory_type="fact",
        body="Contradiction lifecycle says SQLite is durable.",
    )
    config = load_config(vault)

    contradict_memories(config, "mem_20260430_left", "mem_20260430_right")
    doctor = doctor_report(config)

    assert doctor["ok"] is True
    assert doctor["contradiction_count"] == 1
    assert doctor["warnings"][0]["from_id"] == "mem_20260430_left"
    assert doctor["warnings"][0]["to_id"] == "mem_20260430_right"

    reindex_vault(config)
    from agent_memory.brief import brief_memory

    brief = brief_memory(config, "contradiction lifecycle", include_related=True, budget=160)
    assert "Conflict detected: mem_20260430_left contradicts mem_20260430_right." in brief.markdown


def test_doctor_reports_missing_lifecycle_link_targets(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/missing.md",
        memory_id="mem_20260430_missing_link",
        memory_type="decision",
        body="Missing lifecycle links should be reported.",
        supersedes=["mem_20260430_absent"],
    )
    config = load_config(vault)

    payload = doctor_report(config)

    assert payload["ok"] is False
    assert payload["issues"][0]["kind"] == "graph"
    assert payload["issues"][0]["relation"] == "supersedes"
    assert payload["issues"][0]["to_id"] == "mem_20260430_absent"


def test_retrieval_hides_superseded_pending_and_rejected_by_default(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/old.md",
        memory_id="mem_20260430_old",
        memory_type="fact",
        status="active",
        body="Lifecycle filtering default query should hide old memory.",
    )
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Lifecycle filtering default query should show replacement memory.",
        supersedes=["mem_20260430_old"],
    )
    _write_memory(
        vault,
        "Memories/facts/pending.md",
        memory_id="mem_20260430_pending",
        memory_type="fact",
        status="pending",
        body="Lifecycle filtering default query should hide pending memory.",
    )
    _write_memory(
        vault,
        "Memories/facts/rejected.md",
        memory_id="mem_20260430_rejected",
        memory_type="fact",
        status="rejected",
        body="Lifecycle filtering default query should hide rejected memory.",
    )
    config = load_config(vault)
    reindex_vault(config)

    default_ids = [
        result["id"]
        for result in search_memory(config, "lifecycle filtering default query", limit=10).to_dict()["results"]
    ]
    explicit_superseded_ids = [
        result["id"]
        for result in search_memory(
            config,
            "lifecycle filtering default query",
            filters=SearchFilters(status="active"),
            limit=10,
        ).to_dict()["results"]
    ]

    assert default_ids == ["mem_20260430_new"]
    assert "mem_20260430_old" in explicit_superseded_ids
    assert "mem_20260430_pending" not in default_ids
    assert "mem_20260430_rejected" not in default_ids


def test_decay_marks_expired_active_memories_stale(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/expired.md",
        memory_id="mem_20260430_expired",
        memory_type="fact",
        body="Expired valid_to memory should decay to stale.",
        valid_from="2025-01-01",
        valid_to="2026-01-01",
    )
    config = load_config(vault)

    result = decay_memories(config)

    assert result.to_dict()["changed"] == 1
    document = validate_markdown_file(vault / "Memories/facts/expired.md")
    assert document.frontmatter.status == "stale"
    assert document.frontmatter.model_dump(mode="json")["history"][0]["action"] == "decay"


def test_review_queue_only_lists_pending_agent_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/agent.md",
        memory_id="mem_20260430_agent_pending",
        memory_type="fact",
        status="pending",
        body="Pending agent memory appears in review.",
        author_kind="agent",
        source_path="Sources/2026-04-30_lifecycle/source.md",
        confidence=0.7,
    )
    _write_memory(
        vault,
        "Memories/facts/user.md",
        memory_id="mem_20260430_user_pending",
        memory_type="fact",
        status="pending",
        body="Pending user memory does not appear in agent review.",
    )
    config = load_config(vault)

    payload = review_queue(config).to_dict()

    assert payload["pending_count"] == 1
    assert payload["items"][0]["id"] == "mem_20260430_agent_pending"
    assert payload["source_groups"][0]["source"]["path"] == "Sources/2026-04-30_lifecycle/source.md"
    assert payload["source_groups"][0]["memory_ids"] == ["mem_20260430_agent_pending"]


def test_review_queue_groups_pending_memories_by_source(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/first.md",
        memory_id="mem_20260430_first",
        memory_type="fact",
        status="pending",
        body="First pending memory from same source.",
        author_kind="agent",
        source_path="Sources/2026-04-30_shared/extract.md",
        confidence=0.7,
    )
    _write_memory(
        vault,
        "Memories/tasks/second.md",
        memory_id="mem_20260430_second",
        memory_type="task",
        status="pending",
        body="Second pending memory from same source.",
        author_kind="agent",
        source_path="Sources/2026-04-30_shared/extract.md",
        confidence=0.8,
    )
    config = load_config(vault)

    payload = review_queue(config).to_dict()

    assert payload["pending_count"] == 2
    assert len(payload["source_groups"]) == 1
    assert payload["source_groups"][0]["source"]["path"] == "Sources/2026-04-30_shared/extract.md"
    assert payload["source_groups"][0]["item_count"] == 2
    assert set(payload["source_groups"][0]["memory_ids"]) == {"mem_20260430_first", "mem_20260430_second"}


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
    valid_from="2026-04-30",
    valid_to="",
    author_kind="user",
    source_path=None,
    supersedes=None,
    contradicts=None,
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    source_block = (
        "source:\n  path: {0}\n".format(source_path)
        if source_path
        else "source:\n"
    )
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
valid_from: {valid_from}
valid_to: {valid_to}
{source_block}author:
  kind: {author_kind}
  name: test
supersedes: {supersedes}
contradicts: {contradicts}
relations: []
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
            valid_from=valid_from,
            valid_to=valid_to,
            source_block=source_block,
            author_kind=author_kind,
            supersedes=_inline_list(supersedes or []),
            contradicts=_inline_list(contradicts or []),
            body=body,
        ),
        encoding="utf-8",
    )


def _inline_list(values):
    if not values:
        return "[]"
    return "[" + ", ".join(values) + "]"
