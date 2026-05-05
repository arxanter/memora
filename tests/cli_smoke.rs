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
        .args(["search", "SQLite"])
        .assert()
        .success()
        .stdout(contains("decision"))
        .stdout(contains("SQLite"));
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
