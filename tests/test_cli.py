from typer.testing import CliRunner

from cli import app
from config import load_config
from indexer import reindex_vault
from schema import validate_markdown_file
from vault import init_vault


runner = CliRunner()


def test_setup_command_creates_managed_home_layout(tmp_path):
    home = tmp_path / "memora"
    vault = home / "vault"

    result = runner.invoke(app, ["setup", str(home)])

    assert result.exit_code == 0, result.output
    assert "Setup complete:" in result.output
    assert (home / "config.yaml").exists()
    assert (vault / "Memories").is_dir()
    assert (vault / "Sources").is_dir()
    assert (vault / "Wiki" / "index.md").exists()


def test_help_command_lists_current_workflows():
    result = runner.invoke(app, ["help"])

    assert result.exit_code == 0, result.output
    assert "Memora commands" in result.output
    assert "Home and health" in result.output
    assert "Agent setup" in result.output
    assert "source add" in result.output
    assert "wiki lint" in result.output


def test_remember_command_creates_valid_markdown(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    result = runner.invoke(
        app,
        [
            "remember",
            "--type",
            "fact",
            "--text",
            "CLI remember writes durable Markdown.",
            "--vault",
            str(vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Created memory:" in result.output
    [memory_path] = list((vault / "Memories" / "facts").glob("*.md"))
    document = validate_markdown_file(memory_path)
    assert document.frontmatter.type.value == "fact"
    assert document.body.strip() == "CLI remember writes durable Markdown."


def test_review_cli_approves_and_rejects_pending_agent_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/approve.md",
        memory_id="mem_20260505_cli_approve",
        body="CLI review can approve pending agent memories.",
        status="pending",
        author_kind="agent",
        source_path="Sources/2026-05-05_review/extract.md",
        confidence=0.95,
    )
    _write_memory(
        vault,
        "Memories/facts/reject.md",
        memory_id="mem_20260505_cli_reject",
        body="CLI review can reject pending agent memories.",
        status="pending",
        author_kind="agent",
        source_path="Sources/2026-05-05_review/extract.md",
        confidence=0.7,
    )

    list_result = runner.invoke(app, ["review", "--vault", str(vault)])
    approve_result = runner.invoke(
        app,
        ["review", "approve", "mem_20260505_cli_approve", "--vault", str(vault)],
    )
    reject_result = runner.invoke(
        app,
        ["review", "reject", "mem_20260505_cli_reject", "--vault", str(vault)],
    )

    assert list_result.exit_code == 0, list_result.output
    assert "Pending agent memories: 2" in list_result.output
    assert approve_result.exit_code == 0, approve_result.output
    assert "updated mem_20260505_cli_approve: pending -> active" in approve_result.output
    assert reject_result.exit_code == 0, reject_result.output
    assert "updated mem_20260505_cli_reject: pending -> rejected" in reject_result.output
    assert validate_markdown_file(vault / "Memories/facts/approve.md").frontmatter.status == "active"
    assert (
        validate_markdown_file(vault / "Memories/facts/reject.md").frontmatter.status
        == "rejected"
    )


def test_source_add_and_lookup_source_emit_human_output(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    source_file = tmp_path / "source.md"
    extract_file = tmp_path / "extract.md"
    source_file.write_text("Source body for CLI source add.", encoding="utf-8")
    extract_file.write_text("SQLite is only a disposable index cache.", encoding="utf-8")

    add_result = runner.invoke(
        app,
        [
            "source",
            "add",
            str(source_file),
            "--extract",
            str(extract_file),
            "--title",
            "CLI source",
            "--vault",
            str(vault),
        ],
    )

    assert add_result.exit_code == 0, add_result.output
    assert "Saved source:" in add_result.output
    source_id = next((vault / "Sources").iterdir()).name

    lookup_result = runner.invoke(
        app,
        ["lookup-source", source_id, "--query", "SQLite", "--vault", str(vault)],
    )

    assert lookup_result.exit_code == 0, lookup_result.output
    assert f"Source chunks: {source_id}" in lookup_result.output
    assert "SQLite is only" in lookup_result.output


def test_brief_and_build_context_emit_compact_agent_output(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/build-context.md",
        memory_id="mem_20260505_build_context",
        memory_type="decision",
        body="Build-context compact agent output is short for agents.",
    )
    config = load_config(vault)
    reindex_vault(config)

    brief_result = runner.invoke(app, ["brief", "compact agent output", "--vault", str(vault)])
    context_result = runner.invoke(
        app,
        [
            "build-context",
            "What did we decide about compact agent output?",
            "--vault",
            str(vault),
        ],
    )

    assert brief_result.exit_code == 0, brief_result.output
    assert "Memory context:" in brief_result.output
    assert "Build-context compact agent output is short for agents." in brief_result.output
    assert context_result.exit_code == 0, context_result.output
    assert "memory_needed: true" in context_result.output
    assert "Brief:" in context_result.output


def test_agent_rules_command_emits_cli_first_instructions():
    result = runner.invoke(app, ["agent", "rules", "--client", "cursor"])

    assert result.exit_code == 0, result.output
    assert "memora probe" in result.output
    assert "Do not read, write, edit, delete, or migrate Memora vault files directly." in result.output


def _write_memory(
    vault,
    relative_path,
    *,
    memory_id,
    body,
    memory_type="fact",
    status="active",
    author_kind="user",
    source_path=None,
    confidence=None,
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
schema_version: 1
id: {memory_id}
type: {memory_type}
scope: user
project:
status: {status}
confidence: {confidence}
created_at: 2026-05-05T12:00:00+00:00
updated_at: 2026-05-05T12:00:00+00:00
valid_from: 2026-05-05
valid_to:
source:
  path: {source_path}
author:
  kind: {author_kind}
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
            status=status,
            confidence="" if confidence is None else confidence,
            source_path=source_path or "",
            author_kind=author_kind,
            body=body,
        ),
        encoding="utf-8",
    )
    return path
