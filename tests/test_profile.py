from datetime import datetime, timezone

import yaml

from config import load_config
from indexer import estimate_tokens
from memora_profile import build_context_profile_payload, generate_profile_context
from vault import init_vault


def test_generate_user_profile_context_uses_active_user_and_global_memories_without_mutation(
    tmp_path,
):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/user-decision.md",
        memory_id="mem_20260501_user_decision",
        memory_type="decision",
        body="Use generated profiles only as context.",
    )
    _write_memory(
        vault,
        "Memories/preferences/global-preference.md",
        memory_id="mem_20260501_global_preference",
        memory_type="preference",
        scope="global",
        body="Prefer compact profile bullets with citations.",
    )
    _write_memory(
        vault,
        "Memories/facts/project-fact.md",
        memory_id="mem_20260501_project_fact",
        memory_type="fact",
        scope="project",
        project="memora",
        body="Project memory should not enter the user profile.",
    )
    _write_memory(
        vault,
        "Memories/tasks/pending-task.md",
        memory_id="mem_20260501_pending_task",
        memory_type="task",
        status="pending",
        body="Pending memory should not enter any generated profile.",
    )
    memory_paths = sorted((vault / "Memories").rglob("*.md"))
    before = {path: path.read_text(encoding="utf-8") for path in memory_paths}
    config = load_config(vault)

    result = generate_profile_context(
        config,
        profile_type="user",
        budget=500,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    markdown = result.markdown
    frontmatter = yaml.safe_load(markdown.split("---", 2)[1])
    after = {path: path.read_text(encoding="utf-8") for path in memory_paths}

    assert after == before
    assert len(result.items) == 2
    assert result.used_tokens_estimate <= result.budget
    assert frontmatter == {
        "kind": "profile",
        "schema_version": 1,
        "title": "User Profile",
        "aliases": ["User Profile", "Memora User Profile"],
        "profile_type": "user",
        "project": None,
        "generated_at": "2026-05-01T12:00:00+00:00",
        "source_memory_ids": [
            "mem_20260501_user_decision",
            "mem_20260501_global_preference",
        ],
        "token_budget": 500,
        "status": "generated",
    }
    assert "## Decisions" in markdown
    assert "- Use generated profiles only as context. [C1]" in markdown
    assert "## Preferences" in markdown
    assert "- Prefer compact profile bullets with citations. [C2]" in markdown
    assert "[C1] [[Memories/decisions/user-decision|mem_20260501_user_decision]]" in markdown
    assert "(Memories/decisions/user-decision.md)" in markdown
    assert "Project memory should not enter the user profile." not in markdown
    assert "Pending memory should not enter any generated profile." not in markdown
    assert result.citations[0] == {
        "key": "C1",
        "id": "mem_20260501_user_decision",
        "path": "Memories/decisions/user-decision.md",
        "type": "decision",
    }


def test_generate_project_profile_context_filters_exact_project_and_enforces_budget(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/project-a.md",
        memory_id="mem_20260501_project_a",
        memory_type="decision",
        scope="project",
        project="memora",
        body="Include exact project profile memory.",
    )
    _write_memory(
        vault,
        "Memories/decisions/project-b.md",
        memory_id="mem_20260501_project_b",
        memory_type="decision",
        scope="project",
        project="memora",
        body=(
            "This second exact project memory has enough words to be skipped when the "
            "profile reaches the strict token budget."
        ),
    )
    _write_memory(
        vault,
        "Memories/facts/other-project.md",
        memory_id="mem_20260501_other_project",
        memory_type="fact",
        scope="project",
        project="other-project",
        body="Other project memory should be excluded.",
    )
    config = load_config(vault)

    result = generate_profile_context(
        config,
        profile_type="project",
        project="memora",
        budget=95,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    markdown = result.markdown
    frontmatter = yaml.safe_load(markdown.split("---", 2)[1])

    assert result.project == "memora"
    assert len(result.items) == 1
    assert result.truncated is True
    assert result.used_tokens_estimate <= 95
    assert estimate_tokens(markdown) <= 95
    assert frontmatter["title"] == "memora Profile"
    assert frontmatter["aliases"] == [
        "memora Profile",
        "Memora Project Profile: memora",
    ]
    assert frontmatter["source_memory_ids"] == ["mem_20260501_project_a"]
    assert "Include exact project profile memory. [C1]" in markdown
    assert "Memories/decisions/project-a.md" in markdown
    assert "[[Memories/decisions/project-a|mem_20260501_project_a]]" in markdown
    assert "Other project memory should be excluded." not in markdown
    assert "This second exact project memory" not in markdown


def test_build_context_profile_payload_returns_context_without_file_paths(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/project-context/cli.md",
        memory_id="mem_20260501_cli_profile",
        memory_type="project_context",
        scope="project",
        project="memora",
        body="Build context injects profile context in memory.",
    )
    config = load_config(vault)

    payload = build_context_profile_payload(
        config,
        requested=True,
        request_sources=["cli"],
        project="memora",
        task_budget=300,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert payload["included"] is True
    assert payload["reason"] == "included"
    assert payload["profile_type"] == "project"
    assert payload["project"] == "memora"
    assert payload["memory_count"] == 1
    assert "relative_path" not in payload
    assert payload["source_memory_ids"] == ["mem_20260501_cli_profile"]
    assert payload["citations"][0]["key"] == "P1"
    assert "Build context injects profile context in memory. [P1]" in payload["markdown"]


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
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
schema_version: 1
id: {memory_id}
type: {memory_type}
scope: {scope}
project: {project}
status: {status}
confidence:
created_at: 2026-05-01T12:00:00+00:00
updated_at: 2026-05-01T12:00:00+00:00
valid_from: 2026-05-01
valid_to:
source:
author:
  kind: user
  name: test
supersedes: []
contradicts: []
relations: []
observations:
  - category: {memory_type}
    text: {body}
    confidence:
---

{body}
""".format(
            memory_id=memory_id,
            memory_type=memory_type,
            scope=scope,
            project=project or "",
            status=status,
            body=body,
        ),
        encoding="utf-8",
    )
