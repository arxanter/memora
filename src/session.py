"""Ephemeral client-controlled recall session state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class SessionRecallState:
    """IDs the client says are already loaded for the current agent session."""

    session_id: Optional[str] = None
    loaded_memory_ids: tuple[str, ...] = ()
    loaded_source_ids: tuple[str, ...] = ()

    @property
    def requested(self) -> bool:
        return bool(self.session_id or self.loaded_memory_ids or self.loaded_source_ids)

    @property
    def loaded_memory_id_set(self) -> set[str]:
        return set(self.loaded_memory_ids)

    @property
    def loaded_source_id_set(self) -> set[str]:
        return set(self.loaded_source_ids)


def normalize_session_recall_state(
    *,
    session_id: Any = None,
    loaded_memory_ids: Any = None,
    loaded_source_ids: Any = None,
) -> SessionRecallState:
    """Normalize CLI session inputs without persisting state."""

    return SessionRecallState(
        session_id=_optional_string(session_id),
        loaded_memory_ids=normalize_loaded_ids(loaded_memory_ids),
        loaded_source_ids=normalize_loaded_ids(loaded_source_ids),
    )


def normalize_loaded_ids(values: Any) -> tuple[str, ...]:
    """Accept repeated values, comma-separated values, or a single id."""

    if values is None:
        return ()
    if isinstance(values, str):
        raw_values: Iterable[Any] = (values,)
    else:
        try:
            raw_values = tuple(values)
        except TypeError:
            raw_values = (values,)

    selected: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in str(raw).split(","):
            item = part.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            selected.append(item)
    return tuple(selected)


def session_trace(
    state: SessionRecallState,
    *,
    filtered_memory_ids: Any = None,
    filtered_source_ids: Any = None,
    candidate_count_before: Optional[int] = None,
    candidate_count_after: Optional[int] = None,
) -> dict[str, Any]:
    """Return trace metadata only when session inputs or filtering exist."""

    selected_filtered_memory_ids = normalize_loaded_ids(filtered_memory_ids)
    selected_filtered_source_ids = normalize_loaded_ids(filtered_source_ids)
    if (
        not state.requested
        and not selected_filtered_memory_ids
        and not selected_filtered_source_ids
    ):
        return {}

    payload: dict[str, Any] = {
        "session_id": state.session_id,
        "loaded_memory_ids": list(state.loaded_memory_ids),
        "loaded_source_ids": list(state.loaded_source_ids),
        "loaded_memory_count": len(state.loaded_memory_ids),
        "loaded_source_count": len(state.loaded_source_ids),
        "filtered_memory_ids": list(selected_filtered_memory_ids),
        "filtered_source_ids": list(selected_filtered_source_ids),
        "filtered_memory_count": len(selected_filtered_memory_ids),
        "filtered_source_count": len(selected_filtered_source_ids),
        "filtered_count": len(selected_filtered_memory_ids) + len(selected_filtered_source_ids),
    }
    if candidate_count_before is not None:
        payload["candidate_count_before"] = candidate_count_before
    if candidate_count_after is not None:
        payload["candidate_count_after"] = candidate_count_after
    return payload


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


__all__ = [
    "SessionRecallState",
    "normalize_loaded_ids",
    "normalize_session_recall_state",
    "session_trace",
]
