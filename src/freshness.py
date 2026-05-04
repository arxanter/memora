"""Index freshness detection for durable vault files."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from config import MemoryConfig
from indexer import ReindexResult, reindex_vault


@dataclass(frozen=True)
class TrackedFile:
    """Stable file metadata used to detect durable vault changes."""

    path: str
    mtime_ns: int
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "mtime_ns": self.mtime_ns, "size": self.size}


@dataclass(frozen=True)
class FreshnessSnapshot:
    """Snapshot of durable files that should drive index refreshes."""

    files: tuple[TrackedFile, ...]

    @property
    def by_path(self) -> dict[str, TrackedFile]:
        return {item.path: item for item in self.files}

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "files": [item.to_dict() for item in self.files]}


@dataclass(frozen=True)
class FreshnessChange:
    """Difference between two freshness snapshots."""

    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    index_missing: bool = False
    index_stale: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(
            self.added or self.modified or self.removed or self.index_missing or self.index_stale
        )

    @property
    def count(self) -> int:
        return (
            len(self.added)
            + len(self.modified)
            + len(self.removed)
            + int(self.index_missing)
            + len(self.index_stale)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "change_count": self.count,
            "added": list(self.added),
            "modified": list(self.modified),
            "removed": list(self.removed),
            "index_missing": self.index_missing,
            "index_stale": list(self.index_stale),
        }


@dataclass(frozen=True)
class RefreshResult:
    """Outcome from one conservative freshness check."""

    enabled: bool
    checked_files: int
    state_path: Path
    change: FreshnessChange
    reindexed: bool
    clean: bool
    debounce_seconds: float
    reindex: Optional[ReindexResult] = None

    @property
    def ok(self) -> bool:
        return self.reindex.ok if self.reindex else True

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "implemented": True,
            "enabled": self.enabled,
            "checked_files": self.checked_files,
            "state_path": str(self.state_path),
            "reindexed": self.reindexed,
            "clean": self.clean,
            "debounce_seconds": self.debounce_seconds,
            "changes": self.change.to_dict(),
        }
        if self.reindex is not None:
            payload["reindex"] = self.reindex.to_dict()
        return payload


SleepFn = Callable[[float], None]
ReindexFn = Callable[[MemoryConfig], ReindexResult]


def default_freshness_state_path(config: MemoryConfig) -> Path:
    """Return the generated cache file used to remember the last snapshot."""

    return config.vault_path / config.memora_dir / "cache" / "freshness-state.json"


def iter_freshness_files(config: MemoryConfig) -> tuple[Path, ...]:
    """Return durable files that should trigger an index refresh when changed."""

    root = config.vault_path
    local_state_root = root / config.memora_dir
    schema_root = local_state_root / "schemas"
    candidates: list[Path] = []

    candidates.extend(
        path
        for path in root.rglob("*.md")
        if path.is_file() and not _is_relative_to(path, local_state_root)
    )

    config_path = config.config_path
    if config_path.is_file():
        candidates.append(config_path)

    if schema_root.exists():
        candidates.extend(path for path in schema_root.rglob("*") if path.is_file())

    return tuple(sorted(set(candidates)))


def scan_freshness_snapshot(config: MemoryConfig) -> FreshnessSnapshot:
    """Read file metadata for durable freshness inputs."""

    tracked: list[TrackedFile] = []
    for path in iter_freshness_files(config):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        tracked.append(
            TrackedFile(
                path=path.relative_to(config.vault_path).as_posix(),
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            )
        )
    return FreshnessSnapshot(files=tuple(tracked))


def detect_freshness_change(
    current: FreshnessSnapshot,
    *,
    previous: Optional[FreshnessSnapshot] = None,
    index_path: Optional[Path] = None,
) -> FreshnessChange:
    """Detect added, modified, removed, missing-index, and stale-index changes."""

    current_by_path = current.by_path
    index_missing = index_path is None or not index_path.exists()
    index_mtime_ns = None if index_missing else index_path.stat().st_mtime_ns
    index_stale = (
        ()
        if index_mtime_ns is None
        else tuple(sorted(item.path for item in current.files if item.mtime_ns > index_mtime_ns))
    )
    if previous is not None:
        previous_by_path = previous.by_path
        added = tuple(sorted(set(current_by_path) - set(previous_by_path)))
        removed = tuple(sorted(set(previous_by_path) - set(current_by_path)))
        modified = tuple(
            sorted(
                path
                for path in set(current_by_path) & set(previous_by_path)
                if current_by_path[path] != previous_by_path[path]
            )
        )
        return FreshnessChange(
            added=added,
            modified=modified,
            removed=removed,
            index_missing=index_missing,
            index_stale=index_stale,
        )

    if index_missing:
        return FreshnessChange(index_missing=True)

    return FreshnessChange(index_stale=index_stale)


def load_freshness_snapshot(path: Path) -> Optional[FreshnessSnapshot]:
    """Load a persisted snapshot, returning None when it is absent or invalid."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = payload.get("files")
    if not isinstance(files, list):
        return None
    tracked: list[TrackedFile] = []
    for item in files:
        if not isinstance(item, dict):
            return None
        try:
            tracked.append(
                TrackedFile(
                    path=str(item["path"]),
                    mtime_ns=int(item["mtime_ns"]),
                    size=int(item["size"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    return FreshnessSnapshot(files=tuple(tracked))


def save_freshness_snapshot(path: Path, snapshot: FreshnessSnapshot) -> None:
    """Persist the current durable-file snapshot in generated cache state."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def refresh_index_if_needed(
    config: MemoryConfig,
    *,
    state_path: Optional[Path] = None,
    debounce_seconds: Optional[float] = None,
    clean: Optional[bool] = None,
    sleep: SleepFn = time.sleep,
    reindex: Optional[ReindexFn] = None,
) -> RefreshResult:
    """Run `memora reindex` only when tracked durable files changed."""

    resolved_state_path = state_path or default_freshness_state_path(config)
    resolved_debounce = (
        config.index_freshness.debounce_seconds if debounce_seconds is None else debounce_seconds
    )
    resolved_clean = config.index_freshness.clean if clean is None else clean
    current = scan_freshness_snapshot(config)

    if not config.index_freshness.enabled:
        return RefreshResult(
            enabled=False,
            checked_files=len(current.files),
            state_path=resolved_state_path,
            change=FreshnessChange(),
            reindexed=False,
            clean=resolved_clean,
            debounce_seconds=resolved_debounce,
        )

    previous = load_freshness_snapshot(resolved_state_path)
    change = detect_freshness_change(current, previous=previous, index_path=config.index_file)
    if not change.changed:
        save_freshness_snapshot(resolved_state_path, current)
        return RefreshResult(
            enabled=True,
            checked_files=len(current.files),
            state_path=resolved_state_path,
            change=change,
            reindexed=False,
            clean=resolved_clean,
            debounce_seconds=resolved_debounce,
        )

    stable_snapshot = wait_for_quiet_snapshot(config, current, resolved_debounce, sleep=sleep)
    reindex_fn = reindex or (
        lambda active_config: reindex_vault(active_config, clean=resolved_clean)
    )
    reindex_result = reindex_fn(config)
    save_freshness_snapshot(resolved_state_path, stable_snapshot)
    return RefreshResult(
        enabled=True,
        checked_files=len(stable_snapshot.files),
        state_path=resolved_state_path,
        change=change,
        reindexed=True,
        clean=resolved_clean,
        debounce_seconds=resolved_debounce,
        reindex=reindex_result,
    )


def wait_for_quiet_snapshot(
    config: MemoryConfig,
    initial: FreshnessSnapshot,
    debounce_seconds: float,
    *,
    sleep: SleepFn = time.sleep,
) -> FreshnessSnapshot:
    """Wait until tracked file metadata is unchanged across one debounce window."""

    if debounce_seconds <= 0:
        return initial

    snapshot = initial
    while True:
        sleep(debounce_seconds)
        next_snapshot = scan_freshness_snapshot(config)
        if next_snapshot == snapshot:
            return next_snapshot
        snapshot = next_snapshot


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "FreshnessChange",
    "FreshnessSnapshot",
    "RefreshResult",
    "TrackedFile",
    "default_freshness_state_path",
    "detect_freshness_change",
    "iter_freshness_files",
    "load_freshness_snapshot",
    "refresh_index_if_needed",
    "save_freshness_snapshot",
    "scan_freshness_snapshot",
    "wait_for_quiet_snapshot",
]
