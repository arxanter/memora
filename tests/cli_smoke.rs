use assert_cmd::Command;
use predicates::str::contains;
use sha2::{Digest, Sha256};
use std::fs;
use tempfile::tempdir;

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

#[test]
fn setup_creates_managed_home() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["agent-aliases", "set", "Memo", "Память"])
        .assert()
        .success()
        .stdout(contains("Memo, Память"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["agent-aliases", "list"])
        .assert()
        .success()
        .stdout(contains("Memo"))
        .stdout(contains("Память"));
}

#[test]
fn self_management_outputs_install_shell_init_and_completions() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let user_home = temp.path().join("user-home");
    fs::create_dir_all(&user_home).expect("user home");
    let zshrc = user_home.join(".zshrc");
    let fake_binary = temp.path().join("memora-bin");
    let fake_binary_bytes = b"#!/bin/sh\n";
    fs::write(&fake_binary, fake_binary_bytes).expect("fake binary");
    let checksum = sha256_hex(fake_binary_bytes);
    let updated_binary = temp.path().join("memora-bin-updated");
    let updated_binary_bytes = b"#!/bin/sh\necho updated\n";
    fs::write(&updated_binary, updated_binary_bytes).expect("updated binary");
    let updated_checksum = sha256_hex(updated_binary_bytes);

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .env("SHELL", "/bin/zsh")
        .args(["self", "install", "--dry-run"])
        .assert()
        .success()
        .stdout(contains("installed:"))
        .stdout(contains("bin/memora"))
        .stdout(contains("shell_integration: would_install"))
        .stdout(contains("dry_run: true"));
    assert!(!zshrc.exists());

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .env("SHELL", "/bin/zsh")
        .arg("self")
        .arg("install")
        .arg("--from")
        .arg(&fake_binary)
        .arg("--sha256")
        .arg("sha256:deadbeef")
        .assert()
        .failure()
        .stderr(contains("sha256 mismatch"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .env("SHELL", "/bin/zsh")
        .arg("self")
        .arg("install")
        .arg("--from")
        .arg(&fake_binary)
        .arg("--sha256")
        .arg(&checksum)
        .assert()
        .success()
        .stdout(contains("installed:"))
        .stdout(contains("shell_integration: installed"));
    assert_eq!(
        fs::read(home.join("bin").join("memora")).expect("installed binary"),
        fake_binary_bytes
    );
    let shell_integration = fs::read_to_string(&zshrc).expect("zshrc");
    assert!(shell_integration.contains("memora shell integration"));
    assert!(shell_integration.contains("self shell-init zsh"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .env("SHELL", "/bin/zsh")
        .args([
            "self",
            "update",
            "--dry-run",
            "--repo",
            "example/memora",
            "--version",
            "v9.9.9",
            "--no-shell-integration",
        ])
        .assert()
        .success()
        .stdout(contains(
            "https://github.com/example/memora/releases/download/v9.9.9/memora-",
        ))
        .stdout(contains("SHA256SUMS"))
        .stdout(contains("shell_integration: skipped"))
        .stdout(contains("dry_run: true"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .env("SHELL", "/bin/zsh")
        .arg("self")
        .arg("update")
        .arg("--from")
        .arg(&updated_binary)
        .arg("--sha256")
        .arg(&updated_checksum)
        .assert()
        .success()
        .stdout(contains("updated:"))
        .stdout(contains("shell_integration: current"));
    assert_eq!(
        fs::read(home.join("bin").join("memora")).expect("updated binary"),
        updated_binary_bytes
    );

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["self", "shell-init", "zsh"])
        .assert()
        .success()
        .stdout(contains("MEMORA_HOME"))
        .stdout(contains("FASTEMBED_CACHE_DIR"))
        .stdout(contains("alias memora="))
        .stdout(contains("bin"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .args(["self", "completions", "bash"])
        .assert()
        .success()
        .stdout(contains("memora"));
}

#[test]
fn doctor_reports_raw_source_and_wiki_schema_issues() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("doctor")
        .assert()
        .success()
        .stdout(contains("unsupported raw sensitivity: public"))
        .stdout(contains("unsupported source_quality: guessed"))
        .stdout(contains("unsupported wiki type: note"));
}

#[test]
fn doctor_reports_corrupt_memory_without_aborting() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    let facts = home.join("vault/Memories/facts");
    fs::create_dir_all(&facts).expect("facts dir");
    fs::write(
        facts.join("bad.md"),
        r#"---
schema_version: 1
id: mem_bad
type: fact
scope: user
status: active
confidence: [not-a-number]
created_at: 2026-05-05T10:00:00Z
updated_at: 2026-05-05T10:00:00Z
---

bad memory
"#,
    )
    .expect("bad memory");
    fs::write(
        facts.join("good.md"),
        r#"---
schema_version: 1
id: mem_good
type: fact
scope: user
status: active
created_at: 2026-05-05T10:00:00Z
updated_at: 2026-05-05T10:00:00Z
relations:
  - type: related_to
    target: mem_missing
tags: []
---

good memory
"#,
    )
    .expect("good memory");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("doctor")
        .assert()
        .success()
        .stdout(contains("Memories/facts/bad.md"))
        .stdout(contains("relation target not found: mem_missing"));
}

#[test]
fn remember_reindex_and_search_memory() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("reindex")
        .assert()
        .success()
        .stdout(contains("documents_seen: 1"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["search", "SQLite", "--mode", "text"])
        .assert()
        .success()
        .stdout(contains("decision"))
        .stdout(contains("SQLite"));
}

#[test]
fn search_and_context_use_agent_query_variants() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("reindex")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args([
            "search",
            "storage backend",
            "--variant",
            "SQLite FTS5",
            "--mode",
            "text",
        ])
        .assert()
        .success()
        .stdout(contains("planned_queries:"))
        .stdout(contains("SQLite FTS5"))
        .stdout(contains("decision"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args([
            "context",
            "Remi, что решили по хранилищу?",
            "--variant",
            "SQLite FTS5",
            "--mode",
            "text",
        ])
        .assert()
        .success()
        .stdout(contains("planned_queries:"))
        .stdout(contains("## Packed Context"))
        .stdout(contains("citation: `Memories/"))
        .stdout(contains("SQLite FTS5"))
        .stdout(contains("decision"))
        .stdout(contains("packed_budget_used:"));
}

#[test]
fn probe_routes_to_wiki_for_wiki_intent() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    let wiki_dir = home.join("vault/Wiki/concepts");
    fs::create_dir_all(&wiki_dir).expect("wiki dir");
    fs::write(
        wiki_dir.join("retrieval.md"),
        r#"---
title: Retrieval Guide
type: concept
sources:
  - manual
last_updated: 2026-05-05T10:00:00Z
---

Query variants improve discovery across memories and wiki pages.
"#,
    )
    .expect("wiki page");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args([
            "probe",
            "Remi, что wiki знает про discovery?",
            "--intent",
            "wiki",
            "--variant",
            "query variants",
            "--mode",
            "text",
        ])
        .assert()
        .success()
        .stdout(contains("planned_queries:"))
        .stdout(contains("## Wiki"))
        .stdout(contains("Wiki/concepts/retrieval.md"))
        .stdout(contains("has_context: true"));
}

#[test]
fn search_can_include_related_memories() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("reindex")
        .assert()
        .success()
        .stdout(contains("documents_seen: 2"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
fn search_ranking_prefers_recent_memories_when_other_boosts_match() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    let facts = home.join("vault/Memories/facts");
    fs::create_dir_all(&facts).expect("facts dir");
    fs::write(
        facts.join("old.md"),
        r#"---
schema_version: 1
id: mem_old_phoenix
type: fact
scope: user
status: active
confidence: 0.80
created_at: 2020-01-01T00:00:00Z
updated_at: 2020-01-01T00:00:00Z
tags: []
---

phoenix ranking stable signal
"#,
    )
    .expect("old memory");
    fs::write(
        facts.join("recent.md"),
        r#"---
schema_version: 1
id: mem_recent_phoenix
type: fact
scope: user
status: active
confidence: 0.80
created_at: 2026-05-05T00:00:00Z
updated_at: 2026-05-05T00:00:00Z
tags: []
---

phoenix ranking stable signal
"#,
    )
    .expect("recent memory");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("reindex")
        .assert()
        .success()
        .stdout(contains("documents_seen: 2"));

    let output = Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["search", "phoenix", "--mode", "text"])
        .assert()
        .success()
        .get_output()
        .stdout
        .clone();
    let output = String::from_utf8(output).expect("utf8");
    let recent_index = output.find("mem_recent_phoenix").expect("recent result");
    let old_index = output.find("mem_old_phoenix").expect("old result");
    assert!(recent_index < old_index, "{output}");
}

#[test]
fn search_auto_refreshes_missing_index() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .args(["search", "hashed embeddings", "--mode", "vector"])
        .assert()
        .success()
        .stdout(contains("fact"))
        .stdout(contains("embeddings"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_SEMANTIC_PROVIDER", "deterministic")
        .env("MEMORA_SEMANTIC_MODEL", "deterministic-test-v1")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("raw")
        .arg("add")
        .arg(&input)
        .args(["--kind", "text", "--format", "markdown"])
        .assert()
        .success()
        .stdout(contains("raw_id:"));

    let source_output = Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
        .args(["wiki", "ingest", source_id, "--concept", "Rust rewrite"])
        .assert()
        .success()
        .stdout(contains("wrote: Wiki/sources/"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["wiki", "search", "Rust"])
        .assert()
        .success()
        .stdout(contains("Wiki/sources/"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["context", "Rust", "--intent", "evidence", "--mode", "text"])
        .assert()
        .success()
        .stdout(contains("## Sources"))
        .stdout(contains("source_id="));
}

#[test]
fn raw_analyze_creates_extract_template_and_risk_flags() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let input = temp.path().join("incident.md");
    fs::write(
        &input,
        "# Incident\n\nContact admin@example.com and rotate api_key immediately.",
    )
    .expect("input");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    let raw_output = Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("raw")
        .arg("add")
        .arg(&input)
        .args([
            "--kind",
            "text",
            "--format",
            "markdown",
            "--sensitivity",
            "private",
        ])
        .assert()
        .success()
        .stdout(contains("raw_id:"))
        .get_output()
        .stdout
        .clone();
    let raw_output = String::from_utf8(raw_output).expect("utf8");
    let raw_path = raw_output
        .lines()
        .find_map(|line| line.strip_prefix("raw: "))
        .expect("raw path");

    let analyze_output = Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["raw", "analyze", raw_path])
        .assert()
        .success()
        .stdout(contains("risk_flags:"))
        .stdout(contains("possible_secret"))
        .stdout(contains("possible_personal_email"))
        .get_output()
        .stdout
        .clone();
    let analyze_output = String::from_utf8(analyze_output).expect("utf8");
    let analysis_path = analyze_output
        .lines()
        .find_map(|line| line.strip_prefix("analysis: "))
        .expect("analysis path");
    let template = fs::read_to_string(home.join("vault").join(analysis_path)).expect("template");
    assert!(template.contains("## Candidate Memories"));
    assert!(template.contains("memora source add"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["raw", "inspect", raw_path])
        .assert()
        .success()
        .stdout(contains("sensitivity: private"));
}

#[test]
fn agent_integrate_writes_managed_block() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let target = temp.path().join("AGENTS.md");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
fn agent_integrate_all_is_client_specific_and_idempotent() {
    let temp = tempdir().expect("tempdir");
    let home = temp.path().join("memora-home");
    let project = temp.path().join("project");
    fs::create_dir_all(&project).expect("project dir");
    fs::write(project.join("AGENTS.md"), "Existing agent notes.\n").expect("agents");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["agent", "integrate", "--client", "all", "--project"])
        .arg(&project)
        .assert()
        .success()
        .stdout(contains("client=Cursor"))
        .stdout(contains("client=Claude"))
        .stdout(contains("client=Codex"));

    let cursor_rules =
        fs::read_to_string(project.join(".cursor/rules/memora.mdc")).expect("cursor");
    let claude_rules = fs::read_to_string(project.join("CLAUDE.md")).expect("claude");
    let codex_rules = fs::read_to_string(project.join("AGENTS.md")).expect("codex");
    assert!(cursor_rules.contains("alwaysApply: true"));
    assert!(cursor_rules.contains("Client: Cursor"));
    assert!(claude_rules.contains("Client: Claude"));
    assert!(codex_rules.contains("Existing agent notes."));
    assert!(codex_rules.contains("Client: Codex"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["agent", "update", "--client", "all", "--project"])
        .arg(&project)
        .assert()
        .success()
        .stdout(contains("unchanged: client=Cursor"))
        .stdout(contains("unchanged: client=Claude"))
        .stdout(contains("unchanged: client=Codex"));

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .args(["agent", "status", "--client", "all", "--project"])
        .arg(&project)
        .assert()
        .success()
        .stdout(contains("client=Cursor installed=true current=true"))
        .stdout(contains("client=Claude installed=true current=true"))
        .stdout(contains("client=Codex installed=true current=true"));
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
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
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
        .env("MEMORA_HOME", &home)
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
    let user_home = temp.path().join("user-home");
    fs::create_dir_all(&user_home).expect("user home");
    let fake_binary = temp.path().join("memora-bin");
    fs::write(&fake_binary, "#!/bin/sh\n").expect("fake binary");

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("setup")
        .assert()
        .success();

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("HOME", &user_home)
        .env("MEMORA_HOME", &home)
        .arg("self")
        .arg("install")
        .arg("--from")
        .arg(&fake_binary)
        .arg("--no-shell-integration")
        .assert()
        .success()
        .stdout(contains("installed:"));
    assert!(home.join("bin").join("memora").is_file());

    Command::cargo_bin("memora")
        .expect("memora binary")
        .env("MEMORA_HOME", &home)
        .arg("uninstall")
        .assert()
        .success()
        .stdout(contains("vault_preserved: true"));

    assert!(home.join("vault").is_dir());
    assert!(home.join("config.yaml").is_file());
    assert!(!home.join("state").exists());
    assert!(!home.join("bin").join("memora").exists());
}
