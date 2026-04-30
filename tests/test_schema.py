from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_memory.schema import MemoryFrontmatter, parse_markdown_document, validate_vault


SAMPLE_VAULT = Path(__file__).resolve().parents[1] / "examples" / "sample-vault"


def base_frontmatter(**overrides):
    frontmatter = {
        "schema_version": 1,
        "id": "mem_20260429_test01",
        "type": "fact",
        "status": "active",
        "created_at": "2026-04-29T12:00:00+02:00",
        "updated_at": "2026-04-29T12:00:00+02:00",
    }
    frontmatter.update(overrides)
    return frontmatter


def test_parse_valid_markdown_memory():
    document = parse_markdown_document(
        """---
schema_version: 1
id: mem_20260429_abc123
type: decision
scope: project
project: agent-memory
status: active
created_at: 2026-04-29T12:00:00+02:00
updated_at: 2026-04-29T12:00:00+02:00
relations:
  - type: supports
    target: mem_20260429_def456
observations:
  - category: decision
    text: Markdown is the source of truth.
---

Use Markdown as the durable record.
"""
    )

    assert document.frontmatter.id == "mem_20260429_abc123"
    assert document.frontmatter.project == "agent-memory"
    assert document.body.strip() == "Use Markdown as the durable record."


def test_project_scope_requires_project():
    with pytest.raises(ValidationError, match="project-scoped memory must include project"):
        MemoryFrontmatter.model_validate(base_frontmatter(scope="project"))


def test_agent_authored_memory_requires_source_and_confidence():
    with pytest.raises(ValidationError, match="agent-generated memory must include source"):
        MemoryFrontmatter.model_validate(base_frontmatter(author={"kind": "agent"}))

    with pytest.raises(ValidationError, match="agent-generated memory must include confidence"):
        MemoryFrontmatter.model_validate(
            base_frontmatter(author={"kind": "agent"}, source={"path": "Sources/example.md"})
        )


def test_relation_vocabulary_is_enforced():
    with pytest.raises(ValidationError):
        MemoryFrontmatter.model_validate(
            base_frontmatter(relations=[{"type": "blocks", "target": "mem_20260429_other1"}])
        )


def test_migration_field_is_supported():
    frontmatter = MemoryFrontmatter.model_validate(
        base_frontmatter(
            migration={
                "from_schema_version": 0,
                "migrated_at": "2026-04-29T12:00:00+02:00",
                "tool": "test",
            }
        )
    )

    assert frontmatter.migration is not None
    assert frontmatter.migration.from_schema_version == 0


def test_sample_vault_validates():
    report = validate_vault(SAMPLE_VAULT)

    assert report.ok, [issue.message for issue in report.issues]
    assert {document.frontmatter.type for document in report.documents} >= {
        "decision",
        "fact",
        "preference",
        "project_context",
        "source_extract",
        "task",
    }
