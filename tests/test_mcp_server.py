from agent_memory.config import load_config
from agent_memory.indexer import reindex_vault
from agent_memory.mcp_server import (
    brief_tool,
    build_context_tool,
    explain_recall_tool,
    inspect_tool,
    mark_superseded_tool,
    mark_status_tool,
    recall_tool,
    remember_tool,
    search_tool,
    should_recall_tool,
)
from agent_memory.schema import validate_markdown_file
from agent_memory.vault import init_vault


def test_mcp_remember_creates_pending_agent_memory(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = remember_tool(
        {
            "type": "decision",
            "text": "Use MCP handlers as a thin layer over shared services.",
            "scope": "project",
            "project": "agent-memory",
            "confidence": 0.84,
            "source": {
                "path": "Sources/2026-04-30_mcp/source.md",
                "title": "Stage 3 implementation notes",
            },
            "tags": ["mcp", "stage-3"],
        },
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "remember"
    assert payload["type"] == "decision"
    assert payload["status"] == "pending"
    assert payload["review_required"] is True
    assert payload["confidence"] == 0.84
    assert payload["citations"] == [
        {"id": payload["id"], "path": payload["relative_path"], "kind": "memory"}
    ]

    document = validate_markdown_file(vault / payload["relative_path"])
    assert document.frontmatter.id == payload["id"]
    assert document.frontmatter.author is not None
    assert document.frontmatter.author.kind == "agent"
    assert document.frontmatter.status == "pending"
    assert document.frontmatter.confidence == 0.84
    assert document.frontmatter.source is not None
    assert document.frontmatter.source.path == "Sources/2026-04-30_mcp/source.md"
    assert document.body.strip() == "Use MCP handlers as a thin layer over shared services."


def test_mcp_inspect_returns_memory_with_citation(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "fact",
            "text": "MCP inspect reads canonical Markdown by id.",
            "source": "Sources/2026-04-30_mcp/source.md",
        },
        vault=vault,
    )

    payload = inspect_tool(remembered["id"], vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "inspect"
    assert payload["id"] == remembered["id"]
    assert payload["found"] is True
    assert payload["memory"]["id"] == remembered["id"]
    assert payload["memory"]["status"] == "pending"
    assert payload["body"] == "MCP inspect reads canonical Markdown by id."
    assert payload["citations"] == remembered["citations"]


def test_mcp_explain_recall_uses_real_explanation_service(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP explain recall reports selected memory chunks.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = explain_recall_tool("selected memory chunks", 40, {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["tool"] == "explain_recall"
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["id"] == remembered["id"]
    assert payload["selected"][0]["explanation"].startswith("Selected chunk")
    assert payload["citations"][0]["id"] == remembered["id"]


def test_mcp_mark_status_mutates_memory(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "fact",
            "text": "MCP mark status updates lifecycle frontmatter.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )

    payload = mark_status_tool(remembered["id"], "stale", vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "mark_status"
    assert payload["implemented"] is True
    assert payload["mutated"] is True
    assert payload["status"] == "stale"
    assert payload["citations"] == remembered["citations"]
    document = validate_markdown_file(vault / remembered["relative_path"])
    assert document.frontmatter.status == "stale"


def test_mcp_search_uses_retrieval_service(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP search uses the shared keyword retrieval service.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = search_tool("keyword retrieval", {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "search"
    assert payload["implemented"] is True
    assert payload["result_count"] == 1
    assert payload["results"][0]["id"] == remembered["id"]
    assert payload["results"][0]["citation"] == remembered["citations"][0]


def test_mcp_recall_uses_budgeted_packing_service(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP recall packs keyword retrieval chunks under a strict token budget.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = recall_tool("keyword retrieval", 10, {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "recall"
    assert payload["implemented"] is True
    assert payload["budget"] == 10
    assert payload["used_tokens_estimate"] <= 10
    assert payload["chunks"][0]["id"] == remembered["id"]
    assert payload["chunks"][0]["citation"] == payload["citations"][0]
    assert payload["citations"][0]["id"] == remembered["id"]
    assert payload["citations"][0]["path"] == remembered["relative_path"]


def test_mcp_brief_uses_memory_brief_service(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP brief builds citation-preserving Markdown under a strict token budget.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = brief_tool("MCP brief", 90, {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "brief"
    assert payload["implemented"] is True
    assert payload["budget_mode"] == "strict"
    assert payload["used_tokens_estimate"] <= 90
    assert payload["sections"]["warnings"][0]["source_id"] == remembered["id"]
    assert payload["sections"]["warnings"][0]["citations"] == ["C1"]
    assert payload["citations"][0]["path"] == remembered["relative_path"]
    assert "Citations:" in payload["markdown"]


def test_mcp_should_recall_classifies_messages():
    recall_payload = should_recall_tool("Where did we leave off on the previous implementation?")
    no_recall_payload = should_recall_tool("Write a Python function that reverses a list.")

    assert recall_payload["ok"] is True
    assert recall_payload["tool"] == "should_recall"
    assert recall_payload["should_recall"] is True
    assert {trigger["name"] for trigger in recall_payload["triggers"]} & {"earlier_work", "history_or_status"}
    assert no_recall_payload["ok"] is True
    assert no_recall_payload["should_recall"] is False
    assert no_recall_payload["triggers"] == []


def test_mcp_build_context_skips_memory_when_policy_says_no():
    payload = build_context_tool("Write a Python function that reverses a list.", budget=90)

    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["tool"] == "build_context"
    assert payload["task"] == "Write a Python function that reverses a list."
    assert payload["budget"] == 90
    assert payload["memory_needed"] is False
    assert payload["policy"]["should_recall"] is False
    assert payload["policy"]["trigger_count"] == 0
    assert payload["policy"]["triggers"] == []
    assert payload["markdown"] == ""
    assert payload["brief"] is None
    assert payload["citations"] == []


def test_mcp_build_context_returns_brief_when_policy_recommends_recall(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "Build context should call memory brief after the recall policy recommends recall.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = build_context_tool(
        "What did we decide about build context?",
        110,
        {"status": "pending"},
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "build_context"
    assert payload["memory_needed"] is True
    assert payload["policy"]["should_recall"] is True
    assert payload["brief"]["tool"] == "brief"
    assert payload["brief"]["sections"]["warnings"][0]["source_id"] == remembered["id"]
    assert payload["citations"][0]["id"] == remembered["id"]


def test_mcp_mark_superseded_wraps_lifecycle_service(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    old_memory = remember_tool(
        {
            "type": "decision",
            "text": "Old MCP supersede wrapper decision.",
            "source": "Sources/2026-04-30_mcp/source.md",
        },
        vault=vault,
    )
    new_memory = remember_tool(
        {
            "type": "decision",
            "text": "New MCP supersede wrapper decision.",
            "source": "Sources/2026-04-30_mcp/source.md",
        },
        vault=vault,
    )

    payload = mark_superseded_tool(old_memory["id"], new_memory["id"], vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "mark_superseded"
    assert payload["old_id"] == old_memory["id"]
    assert payload["by_id"] == new_memory["id"]
    assert payload["mutated"] is True
    assert payload["relation"] == "supersedes"
    old_document = validate_markdown_file(vault / old_memory["relative_path"])
    new_document = validate_markdown_file(vault / new_memory["relative_path"])
    assert old_document.frontmatter.status == "superseded"
    assert old_memory["id"] in new_document.frontmatter.supersedes


def test_mcp_missing_inspect_has_stable_error_payload(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = inspect_tool("mem_20260430_missing", vault=vault)

    assert payload == {
        "ok": False,
        "tool": "inspect",
        "id": "mem_20260430_missing",
        "found": False,
        "error": {
            "code": "memory_not_found",
            "message": "memory not found: mem_20260430_missing",
        },
        "citations": [],
    }

