from config import load_config
from cli import _context_intent
from vault import init_vault
from wiki import (
    wiki_ingest_source,
    wiki_lint,
    wiki_read,
    wiki_search,
    wiki_status,
    wiki_synthesize,
)


def test_wiki_setup_seeds_core_pages(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config = load_config(vault)

    assert config.wiki_root == vault.resolve() / "Wiki"
    assert (vault / "Wiki" / "index.md").exists()
    assert (vault / "Wiki" / "log.md").exists()
    assert (vault / "Wiki" / "overview.md").exists()
    assert not (vault / "Briefs").exists()


def test_context_intent_routes_between_memory_wiki_and_evidence():
    assert _context_intent("what did we decide about storage", "auto")[0] == "memory"
    assert _context_intent("give me an overview of OpenAI", "auto")[0] == "wiki"
    assert _context_intent("show citation for OpenAI", "auto")[0] == "evidence"
    assert _context_intent("OpenAI enterprise AI", "auto")[0] == "mixed"
    assert _context_intent("anything", "wiki")[0] == "wiki"


def test_wiki_ingest_source_creates_source_entity_concept_and_index(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    source_dir = vault / "Sources" / "2026-05-05_openai"
    source_dir.mkdir(parents=True)
    (source_dir / "extract.md").write_text(
        "---\ntitle: OpenAI enterprise note\n---\n\n"
        "# Extract: OpenAI enterprise note\n\n"
        "OpenAI is relevant to enterprise AI workflow integration.",
        encoding="utf-8",
    )
    config = load_config(vault)

    payload = wiki_ingest_source(
        config,
        "2026-05-05_openai",
        entities=("OpenAI",),
        concepts=("Enterprise AI",),
    )

    assert payload["wiki_page"] == "Wiki/sources/2026-05-05-openai.md"
    assert (vault / "Wiki" / "entities" / "openai.md").exists()
    assert (vault / "Wiki" / "concepts" / "enterprise-ai.md").exists()
    assert "OpenAI enterprise note" in (vault / "Wiki" / "index.md").read_text(encoding="utf-8")
    assert wiki_status(config)["page_count"] >= 6


def test_wiki_search_read_synthesize_and_lint(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    source_dir = vault / "Sources" / "2026-05-05_openai"
    source_dir.mkdir(parents=True)
    (source_dir / "extract.md").write_text(
        "---\ntitle: OpenAI enterprise note\n---\n\n"
        "OpenAI enterprise services connect API access with workflow integration.",
        encoding="utf-8",
    )
    config = load_config(vault)
    wiki_ingest_source(config, "2026-05-05_openai", entities=("OpenAI",))

    search_payload = wiki_search(config, "OpenAI workflow")
    assert search_payload["result_count"] >= 1

    read_payload = wiki_read(config, "Wiki/sources/2026-05-05-openai.md")
    assert read_payload["page"]["type"] == "source"
    assert "workflow integration" in read_payload["page"]["body"]

    synthesis = wiki_synthesize(
        config,
        "What do we know about OpenAI enterprise AI?",
        save=True,
        wiki_results=search_payload["results"],
    )
    assert synthesis["saved"] is True
    assert (vault / synthesis["relative_path"]).exists()

    lint = wiki_lint(config)
    assert lint["page_count"] >= 5
    assert all(issue["kind"] != "broken_link" for issue in lint["issues"])
