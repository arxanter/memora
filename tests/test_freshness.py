from pathlib import Path

from config import load_config
from freshness import (
    FreshnessSnapshot,
    TrackedFile,
    detect_freshness_change,
    iter_freshness_files,
    refresh_index_if_needed,
)
from vault import init_vault


class FakeReindexResult:
    ok = True

    def to_dict(self):
        return {"ok": True, "implemented": True, "index_path": "fake-index.sqlite"}


def test_iter_freshness_files_tracks_durable_markdown_and_config(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    (vault / "Memories" / "facts").mkdir(parents=True, exist_ok=True)
    memory_path = vault / "Memories" / "facts" / "durable.md"
    memory_path.write_text("durable memory", encoding="utf-8")
    source_path = vault / "Sources" / "source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("source extract", encoding="utf-8")
    generated_path = vault / "state" / "cache" / "ignored.md"
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text("generated", encoding="utf-8")

    config = load_config(vault)
    relative_paths = {path.relative_to(vault).as_posix() for path in iter_freshness_files(config)}

    assert "Memories/facts/durable.md" in relative_paths
    assert "Sources/source.md" in relative_paths
    assert "config.yaml" in relative_paths
    assert "state/cache/ignored.md" not in relative_paths


def test_detect_freshness_change_reports_missing_index_even_with_previous_snapshot(tmp_path):
    current = FreshnessSnapshot(files=(TrackedFile("Memories/facts/a.md", 1, 10),))
    previous = FreshnessSnapshot(files=(TrackedFile("Memories/facts/a.md", 1, 10),))

    change = detect_freshness_change(
        current, previous=previous, index_path=tmp_path / "missing.sqlite"
    )

    assert change.changed is True
    assert change.index_missing is True
    assert change.count == 1


def test_refresh_index_if_needed_reindexes_once_then_records_snapshot(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    (vault / "Memories" / "facts").mkdir(parents=True, exist_ok=True)
    (vault / "Memories" / "facts" / "durable.md").write_text("durable memory", encoding="utf-8")
    config = load_config(vault)
    state_path = tmp_path / "freshness-state.json"
    calls: list[Path] = []

    def fake_reindex(active_config):
        calls.append(active_config.vault_path)
        active_config.index_file.parent.mkdir(parents=True, exist_ok=True)
        active_config.index_file.write_text("index", encoding="utf-8")
        return FakeReindexResult()

    first = refresh_index_if_needed(
        config,
        state_path=state_path,
        debounce_seconds=0,
        reindex=fake_reindex,
    )
    second = refresh_index_if_needed(
        config,
        state_path=state_path,
        debounce_seconds=0,
        reindex=fake_reindex,
    )

    assert first.reindexed is True
    assert first.change.changed is True
    assert second.reindexed is False
    assert calls == [config.vault_path]
    assert state_path.exists()
