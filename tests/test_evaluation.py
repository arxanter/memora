import shutil
from pathlib import Path

import yaml

from config import load_config
from evaluation import run_evaluation
from indexer import reindex_vault, split_document_chunks
from schema import validate_markdown_file, validate_vault
from sync import detect_sync_conflicts


FIXTURES = Path(__file__).resolve().parent / "fixtures"
EVAL_SPEC = FIXTURES / "evaluation" / "coding-agent-questions.yaml"


def test_stage12_evaluation_set_runs_representative_coding_agent_questions():
    report = run_evaluation(EVAL_SPEC)
    payload = report.to_dict()

    assert payload["ok"] is True, [
        case for case in payload["cases"] if not case["passed"]
    ]
    assert 30 <= payload["case_count"] <= 50
    assert payload["reindex"]["documents_seen"] >= 15
    assert all(case["used_tokens_estimate"] <= case["max_tokens"] for case in payload["cases"] if case["max_tokens"])


def test_eval_can_run_fixture_directory_smoke_spec():
    report = run_evaluation(FIXTURES / "vault-basic").to_dict()

    assert report["ok"] is True
    assert [case["id"] for case in report["cases"]] == [
        "smoke_recall_sqlite_fixture",
        "smoke_brief_citations_fixture",
    ]


def test_fixture_vaults_validate_reindex_and_rebuild_from_markdown(tmp_path):
    for name in ("vault-basic", "vault-large", "vault-basic-memory-import"):
        source = FIXTURES / name
        assert validate_vault(source).ok
        vault = _copy_fixture(source, tmp_path / name)
        config = load_config(vault)

        first = reindex_vault(config, clean=True)
        config.index_file.unlink()
        rebuilt = reindex_vault(config, clean=True)

        assert first.documents_seen == rebuilt.documents_seen
        assert first.chunks_indexed == rebuilt.chunks_indexed
        assert rebuilt.graph.ok is True


def test_yaml_parsing_schema_migration_and_chunking_from_fixture():
    yaml_doc = validate_markdown_file(FIXTURES / "vault-basic" / "Memories/facts/yaml-parsing-colons.md")
    migrated_doc = validate_markdown_file(FIXTURES / "vault-basic" / "Memories/preferences/migrated-preference.md")
    chunked_doc = validate_markdown_file(FIXTURES / "vault-basic" / "Memories/decisions/sqlite-fts-index.md")

    assert yaml_doc.frontmatter.observations[0].text.endswith("prefer key: value examples.")
    assert migrated_doc.frontmatter.migration is not None
    assert migrated_doc.frontmatter.migration.from_schema_version == 0
    assert {chunk.chunk_type for chunk in split_document_chunks(chunked_doc)} >= {
        "body",
        "section:decision",
        "section:rationale",
        "observation:decision:1",
    }


def test_conflict_fixture_reports_expected_warning_and_conflict_kinds():
    config = load_config(FIXTURES / "vault-conflicts")
    payload = detect_sync_conflicts(config).to_dict()

    assert payload["ok"] is False
    assert payload["conflict_count"] == 3
    assert {issue["kind"] for issue in payload["issues"]} == {
        "conflict_marker",
        "duplicate_id",
        "invalid_frontmatter",
    }


def test_basic_memory_import_fixture_documents_future_compatibility_shape(tmp_path):
    fixture = FIXTURES / "vault-basic-memory-import"
    shape = yaml.safe_load((fixture / "basic-memory-export.yaml").read_text(encoding="utf-8"))
    vault = _copy_fixture(fixture, tmp_path / "import-fixture")
    config = load_config(vault)
    result = reindex_vault(config, clean=True)

    assert shape["expected_memora_shape"]["memory_type"] == "source_extract"
    assert "observations[].text" in shape["expected_memora_shape"]["preserves"]
    assert result.graph.ok is True


def _copy_fixture(source: Path, destination: Path) -> Path:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("index.sqlite", "cache", "embeddings", "locks"),
    )
    return destination
