import yaml

import agent_memory.mcp_server as mcp_server
from agent_memory.config import load_config
from agent_memory.indexer import reindex_vault
from agent_memory.mcp_server import (
    approve_tool,
    brief_tool,
    build_context_tool,
    curate_tool,
    explain_recall_tool,
    ingest_url_tool,
    import_session_tool,
    import_source_inbox_tool,
    import_source_tool,
    inspect_tool,
    mark_superseded_tool,
    mark_status_tool,
    recall_tool,
    remember_tool,
    reject_tool,
    review_tool,
    save_source_tool,
    save_source_with_memories_tool,
    search_tool,
    should_recall_tool,
)
from agent_memory.schema import validate_markdown_file
from agent_memory.vault import init_vault


def _disable_freshness_debounce(vault):
    config_path = vault / ".agent-memory" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("debounce_seconds: 2.0", "debounce_seconds: 0"),
        encoding="utf-8",
    )


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


def test_mcp_remember_can_activate_explicit_user_save_by_policy(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config_path = vault / ".agent-memory" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("trust_level: review", "trust_level: explicit_active"),
        encoding="utf-8",
    )

    payload = remember_tool(
        {
            "type": "decision",
            "text": "Explicit Toby saves can become active when policy allows it.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.91,
            "explicit_user_save": True,
        },
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["status"] == "active"
    assert payload["review_required"] is False
    assert payload["policy"]["trust_level"] == "explicit_active"
    document = validate_markdown_file(vault / payload["relative_path"])
    assert document.frontmatter.status == "active"


def test_mcp_save_source_creates_raw_source_and_extract(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = save_source_tool(
        {
            "title": "Agent memory article",
            "url": "https://example.com/agent-memory",
            "content": "Raw article content about durable memory.",
            "extract": "Summary: Durable facts should be promoted with remember().",
            "project": "agent-memory",
            "tags": ["article", "memory"],
            "channel": "url",
            "source_quality": "agent_fetched",
            "sensitivity": "private",
            "origin": {
                "provider": "web",
                "external_id": "article-123",
            },
        },
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "save_source"
    assert payload["channel"] == "url"
    assert payload["source_quality"] == "agent_fetched"
    assert payload["sensitivity"] == "private"
    assert payload["origin"] == {"provider": "web", "external_id": "article-123"}
    assert payload["relative_source_path"].endswith("/source.md")
    assert payload["relative_extract_path"].endswith("/extract.md")
    assert payload["citations"] == [
        {
            "id": payload["source_id"],
            "path": payload["relative_source_path"],
            "kind": "source",
        },
        {
            "id": payload["source_id"],
            "path": payload["relative_extract_path"],
            "kind": "source_extract",
        },
    ]
    source_text = (vault / payload["relative_source_path"]).read_text(encoding="utf-8")
    extract_text = (vault / payload["relative_extract_path"]).read_text(encoding="utf-8")
    source_frontmatter = yaml.safe_load(source_text.split("---", 2)[1])
    extract_frontmatter = yaml.safe_load(extract_text.split("---", 2)[1])
    assert source_frontmatter["schema_version"] == 1
    assert source_frontmatter["channel"] == "url"
    assert source_frontmatter["source_quality"] == "agent_fetched"
    assert source_frontmatter["sensitivity"] == "private"
    assert source_frontmatter["origin"] == {"provider": "web", "external_id": "article-123"}
    assert extract_frontmatter["channel"] == "url"
    assert extract_frontmatter["source_quality"] == "agent_fetched"
    assert "Source URL: https://example.com/agent-memory" in source_text
    assert "Raw article content about durable memory." in source_text
    assert "Summary: Durable facts should be promoted with remember()." in extract_text
    assert "remember(memory)" in payload["next_steps"][1]


def test_mcp_ingest_url_saves_url_stub_without_fetching(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = ingest_url_tool(
        "https://example.com/no-content-yet",
        title="No content yet",
        project="agent-memory",
        tags=["url"],
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "ingest_url"
    assert payload["url"] == "https://example.com/no-content-yet"
    assert payload["channel"] == "url"
    assert payload["source_quality"] == "unknown"
    assert payload["sensitivity"] == "normal"
    assert payload["relative_extract_path"] is None
    source_text = (vault / payload["relative_source_path"]).read_text(encoding="utf-8")
    assert "No raw content was provided to Agent Memory" in source_text
    assert "https://example.com/no-content-yet" in source_text


def test_mcp_create_server_registers_import_tools(monkeypatch):
    class FakeFastMCP:
        def __init__(self, name):
            self.name = name
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func

            return register

    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_server()

    assert server.name == "Agent Memory"
    assert {"curate", "import_source", "import_source_inbox", "import_session"} <= set(server.tools)


def test_mcp_import_source_saves_file_source_with_metadata(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    source_file = tmp_path / "article.md"
    source_file.write_text("# Imported Article\n\nDurable source content.", encoding="utf-8")

    payload = import_source_tool(
        source_file,
        project="agent-memory",
        tags=["imported"],
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "import_source"
    assert payload["command"] == "import-source"
    assert payload["title"] == "article"
    assert payload["channel"] == "file"
    assert payload["source_quality"] == "imported_export"
    assert payload["origin"] == {
        "provider": "file",
        "file_name": "article.md",
        "path": str(source_file),
    }
    source_text = (vault / payload["relative_source_path"]).read_text(encoding="utf-8")
    source_frontmatter = yaml.safe_load(source_text.split("---", 2)[1])
    assert source_frontmatter["origin"] == payload["origin"]
    assert "# Imported Article" in source_text


def test_mcp_import_source_inbox_dry_run_lists_files_without_writing_sources(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "first.md").write_text("First source", encoding="utf-8")
    (inbox / "second.txt").write_text("Second source", encoding="utf-8")
    (inbox / "skip.json").write_text("{}", encoding="utf-8")

    payload = import_source_inbox_tool(inbox, dry_run=True, vault=vault)

    assert payload["ok"] is True
    assert payload["tool"] == "import_source_inbox"
    assert payload["dry_run"] is True
    assert payload["source_count"] == 2
    assert [source["title"] for source in payload["sources"]] == ["first", "second"]
    assert [source["suffix"] for source in payload["sources"]] == [".md", ".txt"]
    assert not any((vault / "Sources").iterdir())


def test_mcp_import_source_inbox_imports_matching_files(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    inbox = tmp_path / "inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)
    (inbox / "first.md").write_text("First source", encoding="utf-8")
    (nested / "second.markdown").write_text("Second source", encoding="utf-8")
    (inbox / "skip.json").write_text("{}", encoding="utf-8")

    payload = import_source_inbox_tool(
        inbox,
        project="agent-memory",
        tags=["inbox"],
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "import_source_inbox"
    assert payload["dry_run"] is False
    assert payload["source_count"] == 2
    assert [source["title"] for source in payload["sources"]] == ["first", "second"]
    for source in payload["sources"]:
        assert source["project"] == "agent-memory"
        assert source["tags"] == ["inbox"]
        assert source["channel"] == "web_clipper"
        assert (vault / source["relative_source_path"]).exists()


def test_mcp_import_session_saves_transcript_and_pending_summary_memory(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    transcript_file = tmp_path / "session.jsonl"
    transcript_file.write_text('{"role":"user","content":"What changed?"}\n', encoding="utf-8")
    summary = "We added MCP import wrappers for source ingestion."

    payload = import_session_tool(
        transcript_file,
        summary=summary,
        remember_summary=True,
        session_format="cursor-jsonl",
        project="agent-memory",
        tags=["mcp"],
        confidence=0.8,
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "import_session"
    assert payload["source"]["channel"] == "ai_session"
    assert payload["source"]["tags"] == ["mcp", "ai-session"]
    assert payload["source"]["origin"] == {
        "provider": "file",
        "file_name": "session.jsonl",
        "path": str(transcript_file),
        "format": "cursor-jsonl",
    }
    assert payload["source"]["relative_extract_path"].endswith("/extract.md")
    assert payload["memory"]["type"] == "conversation_summary"
    assert payload["memory"]["status"] == "pending"
    assert payload["review_required"] is True
    source_text = (vault / payload["source"]["relative_source_path"]).read_text(encoding="utf-8")
    extract_text = (vault / payload["source"]["relative_extract_path"]).read_text(encoding="utf-8")
    document = validate_markdown_file(vault / payload["memory"]["relative_path"])
    assert "What changed?" in source_text
    assert summary in extract_text
    assert document.frontmatter.status == "pending"
    assert document.frontmatter.type == "conversation_summary"
    assert document.frontmatter.project == "agent-memory"
    assert document.frontmatter.confidence == 0.8
    assert document.frontmatter.source.path == payload["source"]["relative_extract_path"]
    assert document.body.strip() == summary


def test_mcp_save_source_with_memories_creates_pending_atomic_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = save_source_with_memories_tool(
        {
            "title": "Agent workflow notes",
            "url": "https://example.com/workflow",
            "content": "Raw source content.",
            "extract": "## Durable Facts\n- Agents should promote only atomic memories.",
            "project": "agent-memory",
            "tags": ["workflow"],
        },
        [
            {
                "type": "decision",
                "text": "Agent source ingestion stores raw material in Sources and creates pending atomic memories only from explicit durable items.",
                "scope": "project",
                "confidence": 0.82,
                "tags": ["source", "review"],
            },
            {
                "type": "fact",
                "text": "Source promotion memories cite the saved extract when one is available.",
                "confidence": 0.74,
            },
        ],
        author_name="Test agent",
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["tool"] == "save_source_with_memories"
    assert payload["memory_count"] == 2
    assert payload["pending_count"] == 2
    assert payload["review_required"] is True
    assert payload["source"]["relative_extract_path"].endswith("/extract.md")
    assert payload["memories"][0]["status"] == "pending"
    assert payload["memories"][0]["source"]["path"] == payload["source"]["relative_extract_path"]
    assert payload["memories"][0]["source"]["source_id"] == payload["source"]["source_id"]
    assert payload["memories"][0]["author"] == {"kind": "agent", "name": "Test agent"}
    assert "Review the pending atomic memories" in payload["next_steps"][1]

    memory_path = vault / payload["memories"][0]["relative_path"]
    document = validate_markdown_file(memory_path)
    assert document.frontmatter.status == "pending"
    assert document.frontmatter.author.kind == "agent"
    assert document.frontmatter.source.path == payload["source"]["relative_extract_path"]


def test_mcp_save_source_with_memories_rejects_source_extract_promotion(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    payload = save_source_with_memories_tool(
        {"title": "Invalid promotion", "extract": "Summary"},
        [{"type": "source_extract", "text": "Raw summary should stay in Sources."}],
        vault=vault,
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "save_source_with_memories_failed"
    assert "durable atomic memory types" in payload["error"]["message"]


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


def test_mcp_review_approve_and_reject_pending_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    first = remember_tool(
        {
            "type": "fact",
            "text": "MCP review can approve pending memory.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    second = remember_tool(
        {
            "type": "fact",
            "text": "MCP review can reject pending memory.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.6,
        },
        vault=vault,
    )

    review_payload = review_tool(vault=vault)
    approve_payload = approve_tool(first["id"], reason="Looks durable.", vault=vault)
    reject_payload = reject_tool(second["id"], reason="Not durable.", vault=vault)
    after_review = review_tool(vault=vault)

    assert review_payload["ok"] is True
    assert review_payload["tool"] == "review"
    assert review_payload["pending_count"] == 2
    assert {item["id"] for item in review_payload["items"]} == {first["id"], second["id"]}
    assert review_payload["items"][0]["proposed_actions"] == ["approve", "reject", "defer", "inspect"]
    assert "recommended_action" in review_payload["items"][0]

    assert approve_payload["ok"] is True
    assert approve_payload["tool"] == "approve"
    assert approve_payload["status"] == "active"
    assert approve_payload["mutated"] is True
    assert approve_payload["citations"] == first["citations"]

    assert reject_payload["ok"] is True
    assert reject_payload["tool"] == "reject"
    assert reject_payload["status"] == "rejected"
    assert reject_payload["mutated"] is True
    assert reject_payload["citations"] == second["citations"]

    assert after_review["pending_count"] == 0
    assert validate_markdown_file(vault / first["relative_path"]).frontmatter.status == "active"
    assert validate_markdown_file(vault / second["relative_path"]).frontmatter.status == "rejected"


def test_mcp_curate_returns_proposals_without_mutating_memories(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _write_memory(
        vault,
        "Memories/facts/active-duplicate.md",
        memory_id="mem_20260430_active_duplicate",
        memory_type="fact",
        status="active",
        body="MCP curate detects duplicate review text.",
    )
    _write_memory(
        vault,
        "Memories/facts/active-truth.md",
        memory_id="mem_20260430_active_truth",
        memory_type="fact",
        status="active",
        body="MCP curate active memory says Markdown is durable.",
    )
    _write_memory(
        vault,
        "Memories/facts/pending-duplicate.md",
        memory_id="mem_20260430_pending_duplicate",
        memory_type="fact",
        status="pending",
        body="MCP curate detects duplicate review text.",
        author_kind="agent",
        source_path="Sources/2026-04-30_curate_duplicates/extract.md",
    )
    _write_memory(
        vault,
        "Memories/facts/pending-contradiction.md",
        memory_id="mem_20260430_pending_contradiction",
        memory_type="fact",
        status="pending",
        body="MCP curate pending memory contradicts active truth.",
        author_kind="agent",
        source_path="Sources/2026-04-30_curate_contradictions/extract.md",
        contradicts=["mem_20260430_active_truth"],
    )
    memory_paths = sorted((vault / "Memories").rglob("*.md"))
    before = {path: path.read_text(encoding="utf-8") for path in memory_paths}

    payload = curate_tool(vault=vault)

    assert {path: path.read_text(encoding="utf-8") for path in memory_paths} == before
    assert payload["ok"] is True
    assert payload["tool"] == "curate"
    assert payload["pending_count"] == 2
    assert payload["proposal_count"] == 2
    items = {item["id"]: item for item in payload["items"]}
    assert items["mem_20260430_pending_duplicate"]["recommended_action"] == "merge_or_reject_duplicate"
    assert items["mem_20260430_pending_duplicate"]["candidate_summaries"][0]["kind"] == "duplicate"
    assert items["mem_20260430_pending_contradiction"]["recommended_action"] == "inspect_contradiction"
    assert items["mem_20260430_pending_contradiction"]["candidate_summaries"][0]["kind"] == "contradiction"


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


def test_mcp_search_refreshes_index_when_configured(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _disable_freshness_debounce(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP search refreshes the derived index before retrieval when configured.",
            "source": "Sources/2026-05-01_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )

    payload = search_tool("refreshes the derived index", {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["result_count"] == 1
    assert payload["results"][0]["id"] == remembered["id"]
    assert payload["freshness"]["trigger"] == "before_search"
    assert payload["freshness"]["reindexed"] is True


def test_mcp_search_accepts_legacy_boolean_semantic_filter(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP search keeps boolean semantic filters backward compatible.",
            "source": "Sources/2026-04-30_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = search_tool(
        "boolean semantic filters",
        {"status": "pending", "semantic": False, "mode": "hybrid"},
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["mode"] == "text"
    assert payload["requested_mode"] == "semantic:false"
    assert payload["semantic"]["enabled"] is False
    assert payload["results"][0]["id"] == remembered["id"]


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
    assert payload["retrieval"]["planned_query_variants"][0] == "keyword retrieval"
    assert payload["retrieval"]["mode"] == "text"


def test_mcp_recall_refreshes_index_when_configured(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    _disable_freshness_debounce(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "MCP recall refreshes the derived index before packing chunks.",
            "source": "Sources/2026-05-01_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )

    payload = recall_tool("refreshes the derived index", 80, {"status": "pending"}, vault=vault)

    assert payload["ok"] is True
    assert payload["chunks"][0]["id"] == remembered["id"]
    assert payload["freshness"]["trigger"] == "before_recall"
    assert payload["freshness"]["reindexed"] is True


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
    assert payload["retrieval"]["mode"] == "text"
    assert payload["recall"]["retrieval"]["selected_count"] == 1


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
    assert payload["trace"]["policy_query"] == payload["policy"]["query"]
    assert payload["trace"]["planned_query_variants"][0] == payload["policy"]["query"]
    assert payload["trace"]["mode"] == "text"
    assert payload["trace"]["semantic"]["status"] == "not_used"
    assert payload["trace"]["attempted_searches"]
    assert payload["trace"]["freshness"]["trigger"] == "before_recall"
    assert payload["trace"]["selected_count"] == 1


def test_mcp_build_context_applies_task_class_recall_policy(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    remembered = remember_tool(
        {
            "type": "decision",
            "text": "Planning tasks should use the planning recall policy budget.",
            "source": "Sources/2026-05-01_mcp/source.md",
            "confidence": 0.7,
        },
        vault=vault,
    )
    reindex_vault(load_config(vault))

    payload = build_context_tool(
        "What did we decide about planning tasks?",
        filters={"status": "pending", "task_class": "planning"},
        vault=vault,
    )

    assert payload["ok"] is True
    assert payload["memory_needed"] is True
    assert payload["task_class"] == "planning"
    assert payload["budget"] == 2000
    assert payload["brief"]["budget"] == 2000
    assert payload["citations"][0]["id"] == remembered["id"]
    assert payload["trace"]["task_class"] == "planning"
    assert payload["trace"]["recall_policy"]["budget"] == 2000
    assert payload["trace"]["recall_policy"]["include_related"] is True


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


def _write_memory(
    vault,
    relative_path,
    *,
    memory_id,
    memory_type,
    body,
    status,
    author_kind="user",
    source_path=None,
    contradicts=None,
):
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    source_block = "source:\n  path: {0}\n".format(source_path) if source_path else "source:\n"
    path.write_text(
        """---
schema_version: 1
id: {memory_id}
type: {memory_type}
scope: user
project:
status: {status}
confidence: 0.95
created_at: 2026-04-30T12:00:00+02:00
updated_at: 2026-04-30T12:00:00+02:00
valid_from: 2026-04-30
valid_to:
{source_block}author:
  kind: {author_kind}
  name: test
supersedes: []
contradicts: {contradicts}
relations: []
observations:
  - category: {memory_type}
    text: {body}
    confidence: 0.95
---

{body}
""".format(
            memory_id=memory_id,
            memory_type=memory_type,
            status=status,
            source_block=source_block,
            author_kind=author_kind,
            contradicts=_inline_list(contradicts or []),
            body=body,
        ),
        encoding="utf-8",
    )


def _inline_list(values):
    if not values:
        return "[]"
    return "[" + ", ".join(values) + "]"

