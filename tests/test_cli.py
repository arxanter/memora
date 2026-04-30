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
