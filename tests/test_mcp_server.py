from agent_memory.config import load_config
from agent_memory.indexer import reindex_vault
from agent_memory.mcp_server import (
    brief_tool,
    explain_recall_tool,
    inspect_tool,
    mark_status_tool,
    recall_tool,
    remember_tool,
    search_tool,
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


def test_mcp_placeholder_tools_have_golden_payloads(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    vault_path = str(vault.resolve())

    payloads = [
        explain_recall_tool("agent memory", 600, {"scope": "project"}, vault=vault),
        mark_status_tool("mem_20260430_test01", "active", vault=vault),
    ]

    assert payloads == [
        {
            "ok": True,
            "implemented": False,
            "command": "explain_recall",
            "message": "explain_recall is a Stage 2 CLI placeholder; implementation is planned for later stages.",
            "vault_path": vault_path,
            "query": "agent memory",
            "budget": 600,
            "filters": {"scope": "project"},
            "explanation": [],
            "citations": [],
            "tool": "explain_recall",
        },
        {
            "ok": True,
            "implemented": False,
            "command": "mark_status",
            "message": "mark_status is a Stage 2 CLI placeholder; implementation is planned for later stages.",
            "vault_path": vault_path,
            "id": "mem_20260430_test01",
            "status": "active",
            "mutated": False,
            "citations": [],
            "tool": "mark_status",
        },
    ]


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

