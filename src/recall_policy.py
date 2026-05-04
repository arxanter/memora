"""Deterministic automatic recall policy for agent requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


Matcher = Callable[[str], bool]


@dataclass(frozen=True)
class RecallTrigger:
    """One policy trigger that contributed to a recall decision."""

    name: str
    description: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class RecallDecision:
    """Structured automatic recall policy result."""

    message: str
    should_recall: bool
    confidence: float
    triggers: tuple[RecallTrigger, ...]
    query: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "implemented": True,
            "message": self.message,
            "query": self.query,
            "should_recall": self.should_recall,
            "confidence": round(self.confidence, 3),
            "trigger_count": len(self.triggers),
            "triggers": [trigger.to_dict() for trigger in self.triggers],
        }


@dataclass(frozen=True)
class _Rule:
    name: str
    description: str
    weight: float
    matcher: Matcher


_THRESHOLD = 0.6
_DEFAULT_AGENT_ALIASES = ("Remi", "Рэми", "Реми")


def should_recall(message: str, aliases: Sequence[str] | None = None) -> RecallDecision:
    """Classify whether a user request should be enriched with memory."""

    original = str(message or "").strip()
    normalized = _normalize(original)
    selected_aliases = _DEFAULT_AGENT_ALIASES if aliases is None else tuple(aliases)
    alias = _leading_agent_alias(original, selected_aliases)
    alias_triggers = (
        (
            RecallTrigger(
                "memora_alias",
                "User addressed the Memora assistant explicitly.",
                0.99,
            ),
        )
        if alias
        else ()
    )
    triggers = (*alias_triggers, *tuple(_trigger for _trigger in _match_rules(normalized)))
    confidence = min(0.99, sum(trigger.weight for trigger in triggers))
    should_recall_result = confidence >= _THRESHOLD
    return RecallDecision(
        message=original,
        query=_memory_query(original, selected_aliases),
        should_recall=should_recall_result,
        confidence=confidence if should_recall_result else min(confidence, _THRESHOLD - 0.01),
        triggers=triggers,
    )


def _match_rules(text: str) -> Iterable[RecallTrigger]:
    if not text:
        return ()
    if _is_low_context_command(text):
        return ()
    return (
        RecallTrigger(rule.name, rule.description, rule.weight)
        for rule in _RULES
        if rule.matcher(text)
    )


def _normalize(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def _is_low_context_command(text: str) -> bool:
    return _any_match(
        text,
        (
            r"\A(please\s+)?(run|show|execute)?\s*git\s+status\.?\Z",
            r"\A(please\s+)?(run|execute)\s+(tests|test suite)\s+(in|for)\s+this\s+(repo|repository|project)\.?\Z",
        ),
    )


def _leading_agent_alias(message: str, aliases: Sequence[str]) -> str | None:
    cleaned_aliases = tuple(alias.strip() for alias in aliases if alias.strip())
    if not cleaned_aliases:
        return None
    for alias in sorted(cleaned_aliases, key=len, reverse=True):
        pattern = rf"\A\s*{re.escape(alias)}(?:\b|(?=\s|[,.:;!\-]))"
        if re.search(pattern, message, flags=re.IGNORECASE):
            return alias
    return None


def _strip_leading_agent_alias(message: str, aliases: Sequence[str]) -> str:
    cleaned = message.strip()
    for alias in sorted((alias.strip() for alias in aliases if alias.strip()), key=len, reverse=True):
        pattern = rf"\A\s*{re.escape(alias)}(?:\b|(?=\s|[,.:;!\-]))[\s,.:;!\-]*"
        updated = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE).strip()
        if updated != cleaned:
            return updated
    return cleaned


def _memory_query(message: str, aliases: Sequence[str] = _DEFAULT_AGENT_ALIASES) -> str:
    cleaned = re.sub(r"\s+", " ", message.strip())
    query = _strip_leading_agent_alias(cleaned, aliases)
    replacements = (
        r"\Awhat did (we|i) (decide|choose|agree) (about|on|for)\s+",
        r"\Awhat was (previously |earlier |already )?(decided|chosen|agreed) (about|on|for)\s+",
        r"\Awhere did we leave off (on|with|for)?\s*",
        r"\Ain (this|the|our|current) (repo|repository|project|codebase|workspace|app|service|package),?\s*",
        r"\Ahow do we handle\s+",
        r"\Awhat are my\s+",
    )
    for pattern in replacements:
        query = re.sub(pattern, "", query, flags=re.IGNORECASE)
    query = re.sub(
        r"\s+(for|in)\s+(this|the|our|current)\s+(repo|repository|project|codebase|workspace)\??\Z",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = query.strip(" ?.!")
    return query or cleaned


def _previous_decision(text: str) -> bool:
    direct = (
        r"\bwhat did (we|i) (decide|choose|agree)\b",
        r"\bwhat was (previously |earlier |already )?(decided|chosen|agreed)\b",
        r"\b(previous|prior|earlier|last) decision(s)?\b",
    )
    if _any_match(text, direct):
        return True
    return _has_past_marker(text) and _any_match(
        text,
        (
            r"\bdecid(ed|e|ing|ions?)\b",
            r"\b(chose|chosen|choose|agreed|settled|picked|selected)\b",
        ),
    )


def _earlier_work(text: str) -> bool:
    return _any_match(
        text,
        (
            r"\b(previous|prior|earlier|last)\s+(work|change|fix|implementation|stage|task|chat|conversation|session|pr)\b",
            r"\bcontinue\s+from\s+where\s+we\s+(stopped|left off)\b",
            r"\b(as|like)\s+(we|i)\s+(did|discussed|said|planned|implemented|used)\b",
            r"\b(same|that)\s+(approach|pattern|workflow|setup)\s+(as|from)\s+(before|earlier|last time|previously)\b",
            r"\bcontinue\s+(the\s+)?(previous|earlier|last)\s+(work|task|stage|implementation)\b",
            r"\bcontinue\s+(from\s+)?where\s+we\s+(left\s+off|stopped|paused)\b",
            r"\bpick\s+up\s+(where\s+we\s+)?(left\s+off|stopped|paused)\b",
        ),
    )


def _preferences(text: str) -> bool:
    return _any_match(
        text,
        (
            r"\b(my|our|user)\s+(preference|preferences|preferred|coding style|style)\b",
            r"\b(my|our|user)\s+\w+\s+preferences\b",
            r"\b(do|did|have)\s+i\s+(prefer|usually|like)\b",
            r"\bwhat\s+(do|did)\s+i\s+(prefer|usually|like)\b",
            r"\bhow\s+do\s+i\s+like\b",
            r"\bpreferred\s+(style|approach|tool|workflow|format|pattern)\b",
        ),
    )


def _project_question(text: str) -> bool:
    explicit_project_context = _any_match(
        text,
        (
            r"\b(project-specific|repo-specific|codebase-specific)\b",
            r"\bstage\s+\d+\b",
        ),
    )
    if explicit_project_context:
        return True

    project_reference = _any_match(
        text,
        (
            r"\b(in|for|within|inside)\s+(this|the|our|current)\s+(repo|repository|project|codebase|workspace|app|service|package)\b",
            r"\b(this|the|our|current)\s+(repo|repository|project|codebase|workspace|app|service|package)\b",
        ),
    )
    return project_reference and _has_question_intent(text)


def _history_or_status(text: str) -> bool:
    if _any_match(
        text,
        (
            r"\bwhere\s+(are|were)\s+we\b",
            r"\bwhere\s+did\s+we\s+leave\s+off\b",
            r"\bwhat('s| is)\s+(left|done|remaining|next)\s+(for|on|in|with)\s+(the|this|our)?\s*(project|task|implementation|migration|stage|work)\b",
            r"\bwhat('s| is)\s+left\s+to\s+do\b",
            r"\b(recap|summari[sz]e)\s+(our|the|previous|last|project)\s+(work|conversation|history|status|progress)\b",
            r"\b(project|task|implementation|migration|stage)\s+(history|status|progress|timeline)\b",
            r"\bstatus\s+of\s+(the|this|our)\s+(project|task|implementation|migration|stage|work)\b",
        ),
    ):
        return True
    return _has_past_marker(text) and _any_match(
        text,
        (
            r"\b(status|progress|history|timeline|summary|recap)\b",
            r"\b(done|left|remaining|next)\b",
        ),
    )


def _has_question_intent(text: str) -> bool:
    return "?" in text or _any_match(
        text,
        (
            r"\b(how|what|where|why|when|which|who)\b",
            r"\b(conventions?|patterns?|architecture|design|status|history|progress)\b",
        ),
    )


def _has_past_marker(text: str) -> bool:
    return _any_match(
        text,
        (
            r"\b(previously|previous|prior|earlier|before|last time|last session|already)\b",
            r"\bwe\s+(had|did|discussed|planned|decided|agreed|used|implemented)\b",
            r"\bi\s+(had|did|discussed|planned|decided|agreed|used|implemented)\b",
        ),
    )


def _any_match(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


_RULES: tuple[_Rule, ...] = (
    _Rule(
        name="previous_decision",
        description="User asks what was previously decided or agreed.",
        weight=0.9,
        matcher=_previous_decision,
    ),
    _Rule(
        name="earlier_work",
        description="User references earlier work, sessions, changes, or implementation context.",
        weight=0.78,
        matcher=_earlier_work,
    ),
    _Rule(
        name="preferences",
        description="User asks about stored preferences or preferred ways of working.",
        weight=0.85,
        matcher=_preferences,
    ),
    _Rule(
        name="project_specific",
        description="User asks about this repo, project, codebase, workspace, or staged project work.",
        weight=0.65,
        matcher=_project_question,
    ),
    _Rule(
        name="history_or_status",
        description="User asks for history, progress, current status, or where work left off.",
        weight=0.75,
        matcher=_history_or_status,
    ),
)


__all__ = [
    "RecallDecision",
    "RecallTrigger",
    "should_recall",
]
