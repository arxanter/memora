import json
import sqlite3

import pytest
from typer.testing import CliRunner

from cli import app
from config import load_config
from indexer import reindex_vault
from sync import LockTimeout, atomic_write_text, detect_sync_conflicts, vault_lock
from vault import doctor_report, init_vault


runner = CliRunner()


def test_detect_sync_conflicts_reports_markers_duplicate_ids_and_invalid_frontmatter(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(vault, "Memories/facts/one.md", memory_id="mem_20260430_dup", body="First copy.")
    _write_memory(vault, "Memories/facts/two.md", memory_id="mem_20260430_dup", body="Second copy.")
    (vault / "Memories" / "facts" / "broken.md").write_text(
        "---\nid: [unterminated\n---\nBroken.\n",
        encoding="utf-8",
    )
    (vault / "Notes.md").write_text(
        "<<<<<<< HEAD\nLocal note.\n=======\nRemote note.\n>>>>>>> branch\n",
        encoding="utf-8",
    )

    config = load_config(vault)
    report = detect_sync_conflicts(config)
    payload = report.to_dict()
    doctor = doctor_report(config)

    assert report.ok is False
    assert payload["conflict_count"] == 3
    assert {issue["kind"] for issue in payload["issues"]} == {
        "conflict_marker",
        "duplicate_id",
        "invalid_frontmatter",
    }
    duplicate = next(issue for issue in payload["issues"] if issue["kind"] == "duplicate_id")
    assert duplicate["id"] == "mem_20260430_dup"
    assert duplicate["paths"] == ["Memories/facts/one.md", "Memories/facts/two.md"]
    assert doctor["ok"] is False
    assert doctor["conflict_count"] == 3


def test_conflicts_cli_reports_conflicts_and_exits_nonzero(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(vault, "Memories/facts/one.md", memory_id="mem_20260430_dup", body="First copy.")
    _write_memory(vault, "Memories/facts/two.md", memory_id="mem_20260430_dup", body="Second copy.")

    result = runner.invoke(app, ["conflicts", "--vault", str(vault)])

    assert result.exit_code == 1, result.output
    assert "Found 1 Markdown sync conflict(s)." in result.output
    assert "duplicate_id" in result.output


def test_clean_reindex_rebuilds_disposable_index_from_markdown(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/decisions/rebuild.md",
        memory_id="mem_20260430_rebuild",
        memory_type="decision",
        body="Clean reindex rebuilds local working state from Markdown.",
    )
    config = load_config(vault)
    config.index_file.write_text("not a sqlite database", encoding="utf-8")

    result = reindex_vault(config, clean=True)

    assert result.documents_indexed == 1
    assert result.documents_seen == 1
    with sqlite3.connect(config.index_file) as connection:
        match = connection.execute(
            """
            SELECT document_id
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            LIMIT 1
            """,
            ("rebuilds",),
        ).fetchone()
        assert match[0] == "mem_20260430_rebuild"
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_atomic_write_and_vault_lock_helpers(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config = load_config(vault)
    target = vault / "Memories" / "facts" / "atomic.md"

    atomic_write_text(target, "first\n")
    atomic_write_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "second\n"
    assert not list(target.parent.glob(".atomic.md.tmp-*"))

    with vault_lock(config, timeout_seconds=0.1, poll_seconds=0.001) as lock:
        assert lock.path.exists()
        with pytest.raises(LockTimeout):
            with vault_lock(config, timeout_seconds=0.01, poll_seconds=0.001):
                pass
    assert not lock.path.exists()


def _write_memory(
    vault,
    relative_path,
    *,
    memory_id,
    body,
    memory_type="fact",
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
schema_version: 1
id: {memory_id}
type: {memory_type}
status: active
created_at: 2026-04-30T12:00:00+02:00
updated_at: 2026-04-30T12:00:00+02:00
observations:
  - category: {memory_type}
    text: {body}
---

{body}
""".format(
            memory_id=memory_id,
            memory_type=memory_type,
            body=body,
        ),
        encoding="utf-8",
    )
