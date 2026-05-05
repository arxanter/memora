use assert_cmd::Command;
use predicates::str::contains;
use std::fs;
use tempfile::tempdir;

#[test]
fn setup_creates_managed_home() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success()
        .stdout(contains("setup complete"));

    assert!(home.join("config.yaml").is_file());
    assert!(home
        .join("vault")
        .join("Memories")
        .join("decisions")
        .is_dir());
    assert!(home.join("vault").join("Wiki").join("syntheses").is_dir());
    assert!(home.join("state").join("cache").is_dir());
}

#[test]
fn aliases_can_be_reassigned() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["agent-aliases", "set", "Memo", "Память"])
        .assert()
        .success()
        .stdout(contains("Memo, Память"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["agent-aliases", "list"])
        .assert()
        .success()
        .stdout(contains("Memo"))
        .stdout(contains("Память"));
}

#[test]
fn doctor_reports_raw_source_and_wiki_schema_issues() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    let raw_dir = home.join("vault/raw/inbox/text");
    fs::create_dir_all(&raw_dir).expect("raw dir");
    fs::write(raw_dir.join("bad.md"), "raw body").expect("raw body");
    fs::write(
        raw_dir.join("bad.md.meta.yaml"),
        r#"raw_id: raw_bad
kind: text
format: markdown
title: Bad Raw
tags: []
sensitivity: public
captured_at: 2026-05-05T10:00:00Z
original_path: /tmp/bad.md
file_name: bad.md
size_bytes: 8
content_hash: abc123
"#,
    )
    .expect("raw metadata");

    let source_dir = home.join("vault/Sources/src_bad");
    fs::create_dir_all(&source_dir).expect("source dir");
    fs::write(
        source_dir.join("source.md"),
        r#"---
schema_version: 1
source_id: src_bad
kind: source
title: Bad Source
captured_at: 2026-05-05T10:00:00Z
channel: file
source_quality: guessed
sensitivity: normal
tags: []
risk_flags: []
origin: {}
---

source body
"#,
    )
    .expect("source file");

    let wiki_dir = home.join("vault/Wiki/concepts");
    fs::create_dir_all(&wiki_dir).expect("wiki dir");
    fs::write(
        wiki_dir.join("bad.md"),
        r#"---
title: Bad Wiki
type: note
sources:
  - Sources/src_bad/source.md
last_updated: 2026-05-05T10:00:00Z
---

wiki body
"#,
    )
    .expect("wiki file");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("doctor")
        .assert()
        .success()
        .stdout(contains("unsupported raw sensitivity: public"))
        .stdout(contains("unsupported source_quality: guessed"))
        .stdout(contains("unsupported wiki type: note"));
}

#[test]
fn remember_reindex_and_search_memory() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args([
            "remember",
            "--type",
            "decision",
            "--text",
            "Use SQLite FTS5 as the baseline search index.",
            "--project",
            "memory-project",
        ])
        .assert()
        .success()
        .stdout(contains("created:"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("reindex")
        .assert()
        .success()
        .stdout(contains("documents_seen: 1"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["search", "SQLite", "--mode", "text"])
        .assert()
        .success()
        .stdout(contains("decision"))
        .stdout(contains("SQLite"));
}

#[test]
fn search_can_include_related_memories() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    let decisions = home.join("vault/Memories/decisions");
    fs::create_dir_all(&decisions).expect("decisions dir");
    fs::write(
        decisions.join("primary.md"),
        r#"---
schema_version: 1
id: mem_primary
type: decision
scope: project
project: memory-project
status: active
confidence: 0.90
created_at: 2026-05-05T10:00:00Z
updated_at: 2026-05-05T10:00:00Z
relations:
  - type: supports
    target: mem_related
tags: []
---

Blue hummingbird is the direct recall anchor.
"#,
    )
    .expect("primary memory");
    fs::write(
        decisions.join("related.md"),
        r#"---
schema_version: 1
id: mem_related
type: decision
scope: project
project: memory-project
status: active
confidence: 0.80
created_at: 2026-05-05T10:01:00Z
updated_at: 2026-05-05T10:01:00Z
tags: []
---

Related graph evidence should be returned even without direct keyword overlap.
"#,
    )
    .expect("related memory");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("reindex")
        .assert()
        .success()
        .stdout(contains("documents_seen: 2"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args([
            "search",
            "hummingbird",
            "--mode",
            "text",
            "--include-related",
        ])
        .assert()
        .success()
        .stdout(contains("mem_primary"))
        .stdout(contains("mem_related"))
        .stdout(contains("relation=supports"));
}

#[test]
fn search_auto_refreshes_missing_index() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args([
            "remember",
            "--type",
            "fact",
            "--text",
            "Freshness refresh rebuilds the index automatically.",
        ])
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["search", "Freshness", "--mode", "text"])
        .assert()
        .success()
        .stdout(contains("freshness: reason=index_missing"))
        .stdout(contains("Freshness"));
}

#[test]
fn vector_and_hybrid_search_modes_return_memory_candidates() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args([
            "remember",
            "--type",
            "fact",
            "--text",
            "Hybrid retrieval stores local hashed embeddings in SQLite.",
        ])
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_SEMANTIC_PROVIDER", "deterministic")
        .env("MEMORA_SEMANTIC_MODEL", "deterministic-test-v1")
        .arg("--home")
        .arg(&home)
        .args(["search", "hashed embeddings", "--mode", "vector"])
        .assert()
        .success()
        .stdout(contains("fact"))
        .stdout(contains("embeddings"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_SEMANTIC_PROVIDER", "deterministic")
        .env("MEMORA_SEMANTIC_MODEL", "deterministic-test-v1")
        .arg("--home")
        .arg(&home)
        .args(["search", "Hybrid retrieval", "--mode", "hybrid"])
        .assert()
        .success()
        .stdout(contains("fact"))
        .stdout(contains("Hybrid"));
}

#[test]
fn local_command_embedding_provider_is_supported() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let script = temp.path().join("embed.sh");
    fs::write(
        &script,
        r#"#!/bin/sh
printf '{"embeddings":[[1.0,0.0,0.0,0.0]]}'
"#,
    )
    .expect("script");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args([
            "remember",
            "--type",
            "fact",
            "--text",
            "Local command embeddings use a JSON stdin stdout contract.",
        ])
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_SEMANTIC_PROVIDER", "local-command")
        .env("MEMORA_SEMANTIC_MODEL", "test-local-command")
        .env(
            "MEMORA_SEMANTIC_COMMAND",
            format!("sh {}", script.display()),
        )
        .arg("--home")
        .arg(&home)
        .args(["search", "anything", "--mode", "vector"])
        .assert()
        .success()
        .stdout(contains("Local command"))
        .stdout(contains("fact"));
}

#[test]
fn raw_source_and_wiki_capture_flow() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let input = temp.path().join("notes.md");
    let extract = temp.path().join("extract.md");
    fs::write(&input, "# Notes\n\nRust rewrite source material.").expect("input");
    fs::write(&extract, "Rust rewrite source extract.").expect("extract");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("raw")
        .arg("add")
        .arg(&input)
        .args(["--kind", "text", "--format", "markdown"])
        .assert()
        .success()
        .stdout(contains("raw_id:"));

    let source_output = Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("source")
        .arg("add")
        .arg(&input)
        .arg("--extract")
        .arg(&extract)
        .args(["--title", "Rust Rewrite Notes"])
        .assert()
        .success()
        .get_output()
        .stdout
        .clone();
    let source_output = String::from_utf8(source_output).expect("utf8");
    let source_id = source_output
        .lines()
        .find_map(|line| line.strip_prefix("source_id: "))
        .expect("source id");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["wiki", "ingest", source_id, "--concept", "Rust rewrite"])
        .assert()
        .success()
        .stdout(contains("wrote: Wiki/sources/"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["wiki", "search", "Rust"])
        .assert()
        .success()
        .stdout(contains("Wiki/sources/"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["context", "Rust", "--intent", "evidence", "--mode", "text"])
        .assert()
        .success()
        .stdout(contains("## Sources"))
        .stdout(contains("source_id="));
}

#[test]
fn agent_integrate_writes_managed_block() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let target = temp.path().join("AGENTS.md");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .args(["agent", "integrate", "--client", "agents", "--target"])
        .arg(&target)
        .assert()
        .success()
        .stdout(contains("updated:"));

    let content = fs::read_to_string(target).expect("target");
    assert!(content.contains("BEGIN MEMORA MANAGED BLOCK"));
    assert!(content.contains("Auto recall enabled: true"));
}

#[test]
fn session_finalize_saves_source_and_pending_memory() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let transcript = temp.path().join("transcript.md");
    let summary = temp.path().join("summary.md");
    let memories = temp.path().join("memories.json");
    fs::write(&transcript, "# Transcript\n\nWe decided to keep CLI first.").expect("transcript");
    fs::write(&summary, "Session summary: CLI first.").expect("summary");
    fs::write(
        &memories,
        r#"[{"type":"decision","text":"Keep the Rust rewrite CLI-first.","tags":["session"]}]"#,
    )
    .expect("memories");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("session")
        .arg("finalize")
        .arg(&transcript)
        .arg("--summary-file")
        .arg(&summary)
        .arg("--memories-file")
        .arg(&memories)
        .args(["--project", "memory-project"])
        .assert()
        .success()
        .stdout(contains("source_id:"))
        .stdout(contains("pending_memories: 1"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("review")
        .arg("list")
        .assert()
        .success()
        .stdout(contains("decision"));
}

#[test]
fn uninstall_preserves_vault_by_default() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .arg("--home")
        .arg(&home)
        .arg("uninstall")
        .assert()
        .success()
        .stdout(contains("vault_preserved: true"));

    assert!(home.join("vault").is_dir());
    assert!(!home.join("state").exists());
}
