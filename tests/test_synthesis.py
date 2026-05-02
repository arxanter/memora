import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_memory.cli import app
from agent_memory.config import load_config
from agent_memory.synthesis import write_synthesis
from agent_memory.vault import init_vault


runner = CliRunner()


def test_write_synthesis_writes_grouped_cited_active_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/decision.md",
        memory_id="mem_20260501_decision",
        memory_type="decision",
        body="Use Markdown as durable memory for synthesis output.",
    )
    _write_memory(
        vault,
        "Memories/facts/fact.md",
        memory_id="mem_20260501_fact",
        memory_type="fact",
        body="SQLite remains a rebuildable cache for synthesis inputs.",
    )
    _write_memory(
        vault,
        "Memories/tasks/pending.md",
        memory_id="mem_20260501_pending",
        memory_type="task",
        status="pending",
        body="Pending synthesis memory should be excluded.",
    )
    _write_memory(
        vault,
        "Memories/facts/rejected.md",
        memory_id="mem_20260501_rejected",
        memory_type="fact",
        status="rejected",
        body="Rejected synthesis memory should be excluded.",
    )
    _write_memory(
        vault,
        "Memories/facts/superseded.md",
        memory_id="mem_20260501_superseded",
        memory_type="fact",
        status="superseded",
        body="Superseded synthesis memory should be excluded.",
    )
    config = load_config(vault)

    result = write_synthesis(
        config,
        title="Phase 3 Synthesis",
        limit=10,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    payload = result.to_dict()
    synthesis_path = vault / payload["relative_path"]
    markdown = synthesis_path.read_text(encoding="utf-8")

    assert payload["command"] == "synthesize"
    assert payload["implemented"] is True
    assert payload["relative_path"] == "Synthesis/2026-05-01_phase-3-synthesis.md"
    assert payload["memory_count"] == 2
    assert [citation["id"] for citation in payload["citations"]] == [
        "mem_20260501_decision",
        "mem_20260501_fact",
    ]
    assert "schema: agent-memory.synthesis.v1" in markdown
    assert "kind: generated_synthesis" in markdown
    assert "aliases:" in markdown
    assert "## Decisions" in markdown
    assert "- Use Markdown as durable memory for synthesis output. [C1]" in markdown
    assert "## Facts" in markdown
    assert "- SQLite remains a rebuildable cache for synthesis inputs. [C2]" in markdown
    assert "[C1] [[Memories/decisions/decision|mem_20260501_decision]]" in markdown
    assert "(../Memories/decisions/decision.md)" in markdown
    assert "Pending synthesis memory should be excluded." not in markdown
    assert "Rejected synthesis memory should be excluded." not in markdown
    assert "Superseded synthesis memory should be excluded." not in markdown


def test_write_synthesis_filters_project_and_does_not_mutate_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/project.md",
        memory_id="mem_20260501_project",
        memory_type="decision",
        scope="project",
        project="agent-memory",
        body="Project synthesis should include exact project matches.",
    )
    _write_memory(
        vault,
        "Memories/preferences/user.md",
        memory_id="mem_20260501_user",
        memory_type="preference",
        body="User scope memory should not be pulled into project synthesis.",
    )
    _write_memory(
        vault,
        "Memories/facts/other.md",
        memory_id="mem_20260501_other",
        memory_type="fact",
        scope="project",
        project="other-project",
        body="Other project memory should not be pulled into project synthesis.",
    )
    memory_paths = sorted((vault / "Memories").rglob("*.md"))
    before = {path: path.read_text(encoding="utf-8") for path in memory_paths}
    config = load_config(vault)

    result = write_synthesis(
        config,
        project="agent-memory",
        now=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    markdown = (vault / result.relative_path).read_text(encoding="utf-8")
    assert {path: path.read_text(encoding="utf-8") for path in memory_paths} == before
    assert result.to_dict()["relative_path"] == "Synthesis/2026-05-01_agent-memory-synthesis.md"
    assert result.to_dict()["memory_count"] == 1
    assert "Project synthesis should include exact project matches." in markdown
    assert "User scope memory should not be pulled into project synthesis." not in markdown
    assert "Other project memory should not be pulled into project synthesis." not in markdown
    assert "Promote durable conclusions with `memory remember`" in result.to_dict()["next_steps"][1]


def test_synthesize_cli_json_and_help_listing(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/cli.md",
        memory_id="mem_20260501_cli",
        memory_type="decision",
        scope="project",
        project="agent-memory",
        body="CLI synthesis should write a generated Markdown file.",
    )

    result = runner.invoke(
        app,
        [
            "synthesize",
            "--vault",
            str(vault),
            "--project",
            "agent-memory",
            "--title",
            "CLI Synthesis",
            "--limit",
            "5",
            "--json",
        ],
    )
    help_result = runner.invoke(app, ["help", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    relative_path = Path(payload["relative_path"])
    assert payload["command"] == "synthesize"
    assert payload["implemented"] is True
    assert payload["memory_count"] == 1
    assert payload["citations"][0]["path"] == "Memories/decisions/cli.md"
    assert relative_path.parent == Path("Synthesis")
    assert relative_path.name.endswith("_cli-synthesis.md")
    assert (vault / relative_path).exists()

    assert help_result.exit_code == 0, help_result.output
    command_usages = {
        command["usage"]
        for group in json.loads(help_result.output)["groups"]
        for command in group["commands"]
    }
    assert "synthesize" in command_usages


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
