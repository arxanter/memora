import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cli import app
from config import ProfileConfig, load_config
from indexer import estimate_tokens
from memora_profile import build_profile
from vault import init_vault


runner = CliRunner()


def test_build_user_profile_writes_active_user_and_global_memories_without_mutation(tmp_path):
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

    result = build_profile(
        config,
        profile_type="user",
        budget=500,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    payload = result.to_dict()
    markdown = (vault / payload["relative_path"]).read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(markdown.split("---", 2)[1])
    after = {path: path.read_text(encoding="utf-8") for path in memory_paths}

    assert after == before
    assert payload["command"] == "build_profile"
    assert payload["tool"] == "build_profile"
    assert payload["relative_path"] == "Profiles/user.md"
    assert payload["memory_count"] == 2
    assert payload["generated_context"] is True
    assert payload["canonical_memory"] is False
    assert payload["used_tokens_estimate"] <= payload["budget"]
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
    assert "(../Memories/decisions/user-decision.md)" in markdown
    assert "Project memory should not enter the user profile." not in markdown
    assert "Pending memory should not enter any generated profile." not in markdown
    assert payload["citations"][0] == {
        "key": "C1",
        "id": "mem_20260501_user_decision",
        "path": "Memories/decisions/user-decision.md",
        "type": "decision",
    }
    assert "generated context, not canonical memory" in payload["next_steps"][0]


def test_build_project_profile_filters_exact_project_and_enforces_budget(tmp_path):
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

    result = build_profile(
        config,
        profile_type="project",
        project="memora",
        budget=95,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    payload = result.to_dict()
    markdown = (vault / payload["relative_path"]).read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(markdown.split("---", 2)[1])

    assert payload["relative_path"] == "Profiles/projects/memora.md"
    assert payload["project"] == "memora"
    assert payload["memory_count"] == 1
    assert payload["truncated"] is True
    assert payload["used_tokens_estimate"] <= 95
    assert estimate_tokens(markdown) <= 95
    assert frontmatter["title"] == "memora Profile"
    assert frontmatter["aliases"] == [
        "memora Profile",
        "Memora Project Profile: memora",
    ]
    assert frontmatter["source_memory_ids"] == ["mem_20260501_project_a"]
    assert "Include exact project profile memory. [C1]" in markdown
    assert "../../Memories/decisions/project-a.md" in markdown
    assert "[[Memories/decisions/project-a|mem_20260501_project_a]]" in markdown
    assert "Other project memory should be excluded." not in markdown
    assert "This second exact project memory" not in markdown


def test_build_profile_uses_configured_default_budgets_and_explicit_override(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/preferences/user-default-budget.md",
        memory_id="mem_20260501_user_default_budget",
        memory_type="preference",
        body="Profile builders should use the configured user budget.",
    )
    _write_memory(
        vault,
        "Memories/project-context/project-default-budget.md",
        memory_id="mem_20260501_project_default_budget",
        memory_type="project_context",
        scope="project",
        project="memora",
        body="Project profiles should use the configured project budget.",
    )
    config = load_config(vault).model_copy(
        update={"profile": ProfileConfig(user_budget=321, project_budget=654)}
    )

    user_result = build_profile(
        config,
        profile_type="user",
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )
    project_result = build_profile(
        config,
        profile_type="project",
        project="memora",
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )
    explicit_result = build_profile(
        config,
        profile_type="user",
        budget=777,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    user_frontmatter = yaml.safe_load(user_result.markdown.split("---", 2)[1])
    project_frontmatter = yaml.safe_load(project_result.markdown.split("---", 2)[1])
    explicit_frontmatter = yaml.safe_load(explicit_result.markdown.split("---", 2)[1])
    assert user_result.budget == 321
    assert user_frontmatter["token_budget"] == 321
    assert project_result.budget == 654
    assert project_frontmatter["token_budget"] == 654
    assert explicit_result.budget == 777
    assert explicit_frontmatter["token_budget"] == 777


def test_build_profile_respects_disabled_config(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config = load_config(vault).model_copy(update={"profile": ProfileConfig(enabled=False)})

    with pytest.raises(ValueError, match="profile generation is disabled"):
        build_profile(config, profile_type="user", budget=500)

    assert not (vault / "Profiles" / "user.md").exists()


def test_build_profile_cli_json_help_listing_and_project_requirement(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/project-context/cli.md",
        memory_id="mem_20260501_cli_profile",
        memory_type="project_context",
        scope="project",
        project="memora",
        body="CLI profile generation writes under Profiles.",
    )

    result = runner.invoke(
        app,
        [
            "build-profile",
            "--vault",
            str(vault),
            "--type",
            "project",
            "--project",
            "memora",
            "--budget",
            "300",
            "--json",
        ],
    )
    missing_project = runner.invoke(
        app,
        [
            "build-profile",
            "--vault",
            str(vault),
            "--type",
            "project",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "build_profile"
    assert payload["relative_path"] == "Profiles/projects/memora.md"
    assert payload["memory_count"] == 1
    assert (vault / payload["relative_path"]).exists()

    assert missing_project.exit_code == 1
    error_payload = json.loads(missing_project.output)
    assert error_payload["ok"] is False
    assert error_payload["error"]["code"] == "build_profile_failed"
    assert "requires project" in error_payload["error"]["message"]


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
