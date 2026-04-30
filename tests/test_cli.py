import json

from typer.testing import CliRunner

from agent_memory.cli import app
from agent_memory.schema import validate_markdown_file


runner = CliRunner()


def test_init_command_creates_vault_layout(tmp_path):
    vault = tmp_path / "memory-vault"

    result = runner.invoke(app, ["init", str(vault), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["config_created"] is True
    assert (vault / ".agent-memory" / "config.yaml").exists()
    assert (vault / "Memories" / "decisions").is_dir()
    assert (vault / "Profiles" / "projects").is_dir()


def test_remember_command_creates_valid_markdown(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])

    result = runner.invoke(
        app,
        [
            "remember",
            "--vault",
            str(vault),
            "--type",
            "decision",
            "--text",
            "Use Markdown as durable memory.",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    memory_path = vault / payload["relative_path"]
    document = validate_markdown_file(memory_path)

    assert payload["ok"] is True
    assert payload["type"] == "decision"
    assert payload["status"] == "active"
    assert document.frontmatter.id == payload["id"]
    assert document.frontmatter.observations[0].text == "Use Markdown as durable memory."
    assert document.body.strip() == "Use Markdown as durable memory."


def test_status_and_doctor_emit_json(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])

    status_result = runner.invoke(app, ["status", "--vault", str(vault), "--json"])
    doctor_result = runner.invoke(app, ["doctor", "--vault", str(vault), "--json"])

    assert status_result.exit_code == 0, status_result.output
    assert doctor_result.exit_code == 0, doctor_result.output
    assert json.loads(status_result.output)["ok"] is True
    assert json.loads(doctor_result.output)["ok"] is True


def test_mcp_config_command_prints_client_config(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    command = tmp_path / "bin" / "memory-mcp"
    command.parent.mkdir()
    command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--vault",
            str(vault),
            "--command",
            str(command),
        ],
    )
    json_result = runner.invoke(
        app,
        [
            "mcp-config",
            "--vault",
            str(vault),
            "--command",
            str(command),
            "--format",
            "claude",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    config = json.loads(result.output)
    assert config["mcpServers"]["agent-memory"]["command"] == str(command.resolve())
    assert config["mcpServers"]["agent-memory"]["env"]["AGENT_MEMORY_VAULT"] == str(vault.resolve())

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["format"] == "claude"
    assert payload["config"] == config


def test_placeholder_commands_have_stable_json_signatures(tmp_path):
    vault = tmp_path / "memory-vault"
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")
    runner.invoke(app, ["init", str(vault), "--json"])

    commands = [
        ["import", str(source), "--vault", str(vault), "--json"],
        ["export", "--format", "markdown", "--vault", str(vault), "--json"],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["implemented"] is False


def test_brief_command_generates_markdown_and_json(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    runner.invoke(
        app,
        [
            "remember",
            "--vault",
            str(vault),
            "--type",
            "decision",
            "--text",
            "Memory brief CLI returns citation-preserving Markdown.",
            "--json",
        ],
    )
    runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    markdown_result = runner.invoke(app, ["brief", "memory brief", "--vault", str(vault)])
    json_result = runner.invoke(app, ["brief", "memory brief", "--vault", str(vault), "--json"])

    assert markdown_result.exit_code == 0, markdown_result.output
    assert "## Memory Brief" in markdown_result.output
    assert "Current decisions:" in markdown_result.output
    assert "[C1]" in markdown_result.output
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["budget_mode"] == "strict"
    assert payload["used_tokens_estimate"] <= payload["budget"]
    assert payload["markdown"] == markdown_result.output
    assert payload["sections"]["current_decisions"][0]["citations"] == ["C1"]


def test_should_recall_command_emits_human_and_json_output():
    human_result = runner.invoke(app, ["should-recall", "What did we decide about embeddings?"])
    json_result = runner.invoke(
        app,
        ["should-recall", "Write a Python function that reverses a list.", "--json"],
    )

    assert human_result.exit_code == 0, human_result.output
    assert "Recall recommended" in human_result.output
    assert "previous_decision" in human_result.output
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["should_recall"] is False
    assert payload["triggers"] == []


def test_recall_command_packs_indexed_chunks_under_budget(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    runner.invoke(
        app,
        [
            "remember",
            "--vault",
            str(vault),
            "--type",
            "decision",
            "--text",
            "Use token budget packing for keyword memory recall results.",
            "--json",
        ],
    )
    runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    result = runner.invoke(
        app,
        ["recall", "token budget", "--budget", "12", "--vault", str(vault), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["budget"] == 12
    assert payload["used_tokens_estimate"] <= 12
    assert payload["chunk_count"] == 1
    assert payload["chunks"][0]["citation"] == payload["citations"][0]


def test_search_command_returns_ranked_json_results(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    runner.invoke(
        app,
        [
            "remember",
            "--vault",
            str(vault),
            "--type",
            "decision",
            "--scope",
            "project",
            "--project",
            "agent-memory",
            "--text",
            "Use SQLite FTS for keyword memory search.",
            "--json",
        ],
    )
    runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    result = runner.invoke(
        app,
        [
            "search",
            "keyword memory",
            "--vault",
            str(vault),
            "--project",
            "agent-memory",
            "--type",
            "decision",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["result_count"] == 1
    assert payload["results"][0]["metadata"]["project"] == "agent-memory"
    assert payload["results"][0]["citation"]["path"].startswith("Memories/decisions/")


def test_reindex_command_builds_sqlite_index(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    runner.invoke(
        app,
        [
            "remember",
            "--vault",
            str(vault),
            "--type",
            "decision",
            "--text",
            "Use SQLite FTS for the first keyword index.",
            "--json",
        ],
    )

    result = runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["documents_indexed"] == 1
    assert payload["documents_skipped"] == 0
    assert payload["graph_ok"] is True
    assert (vault / ".agent-memory" / "index.sqlite").exists()


def test_stage13_inspect_open_and_graph_cli_outputs(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    _write_memory(
        vault,
        "Memories/facts/old.md",
        memory_id="mem_20260430_old",
        memory_type="fact",
        body="Stage thirteen graph old memory.",
    )
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Stage thirteen graph new memory.",
        supersedes=["mem_20260430_old"],
    )
    runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    inspect_json = runner.invoke(app, ["inspect", "mem_20260430_new", "--vault", str(vault), "--json"])
    inspect_human = runner.invoke(app, ["inspect", "mem_20260430_new", "--vault", str(vault)])
    open_result = runner.invoke(app, ["open", "mem_20260430_new", "--vault", str(vault), "--json"])
    graph_json = runner.invoke(app, ["graph", "mem_20260430_new", "--vault", str(vault), "--json"])
    graph_human = runner.invoke(app, ["graph", "mem_20260430_new", "--vault", str(vault)])

    assert inspect_json.exit_code == 0, inspect_json.output
    inspect_payload = json.loads(inspect_json.output)
    assert inspect_payload["ok"] is True
    assert inspect_payload["implemented"] is True
    assert inspect_payload["relative_path"] == "Memories/decisions/new.md"
    assert inspect_payload["obsidian_uri"].startswith("obsidian://open?path=")
    assert "Stage thirteen graph new memory." in inspect_human.output

    assert open_result.exit_code == 0, open_result.output
    open_payload = json.loads(open_result.output)
    assert open_payload["opened"] is False
    assert open_payload["launch_requested"] is False
    assert open_payload["path"].endswith("Memories/decisions/new.md")

    assert graph_json.exit_code == 0, graph_json.output
    graph_payload = json.loads(graph_json.output)
    assert graph_payload["ok"] is True
    assert graph_payload["source"] == "index"
    assert graph_payload["outgoing"][0]["relation"] == "supersedes"
    assert graph_payload["outgoing"][0]["other"]["id"] == "mem_20260430_old"
    assert "Outgoing" in graph_human.output
    assert "supersedes" in graph_human.output


def test_stage13_explain_recall_cli_reports_selected_and_skipped(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    _write_memory(
        vault,
        "Memories/facts/old.md",
        memory_id="mem_20260430_old",
        memory_type="fact",
        body="Stage thirteen recall explanation selects replacement memory.",
    )
    _write_memory(
        vault,
        "Memories/decisions/new.md",
        memory_id="mem_20260430_new",
        memory_type="decision",
        body="Stage thirteen recall explanation selects replacement memory.",
        supersedes=["mem_20260430_old"],
    )
    runner.invoke(app, ["reindex", "--vault", str(vault), "--json"])

    json_result = runner.invoke(
        app,
        ["explain-recall", "stage thirteen recall explanation", "--budget", "12", "--vault", str(vault), "--json"],
    )
    human_result = runner.invoke(
        app,
        ["explain-recall", "stage thirteen recall explanation", "--budget", "12", "--vault", str(vault)],
    )

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["id"] == "mem_20260430_new"
    assert any(item["reason"] == "superseded" for item in payload["skipped"])
    assert "Selected chunk mem_20260430_new" in human_result.output
    assert "Skipped chunk mem_20260430_old" in human_result.output


def test_stage13_review_human_output_uses_diff_preview(tmp_path):
    vault = tmp_path / "memory-vault"
    runner.invoke(app, ["init", str(vault), "--json"])
    _write_memory(
        vault,
        "Memories/facts/pending-agent.md",
        memory_id="mem_20260430_pending_agent",
        memory_type="fact",
        status="pending",
        body="Pending agent memory body appears in diff preview.",
        author_kind="agent",
        source_path="Sources/stage13.md",
        confidence=0.7,
    )

    result = runner.invoke(app, ["review", "--vault", str(vault)])

    assert result.exit_code == 0, result.output
    assert "diff -- memory/mem_20260430_pending_agent" in result.output
    assert "+ status: pending" in result.output
    assert "+ source: Sources/stage13.md" in result.output
    assert "+ Pending agent memory body appears in diff preview." in result.output


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
    author_kind="user",
    source_path=None,
    supersedes=None,
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
created_at: 2026-04-30T12:00:00+02:00
updated_at: 2026-04-30T12:00:00+02:00
valid_from: 2026-04-30
valid_to:
{source_block}author:
  kind: {author_kind}
  name: test
supersedes: {supersedes}
contradicts: []
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
            source_block=source_block,
            author_kind=author_kind,
            supersedes=_inline_list(supersedes or []),
            body=body,
        ),
        encoding="utf-8",
    )


def _inline_list(values):
    if not values:
        return "[]"
    return "[" + ", ".join(values) + "]"
