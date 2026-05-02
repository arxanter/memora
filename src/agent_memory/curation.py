"""Conservative deterministic signals for review-time memory curation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Sequence

NEAR_DUPLICATE_THRESHOLD = 0.92
CONTRADICTION_SUBJECT_THRESHOLD = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NEGATED_USE_RE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+(?:use|prefer|choose|adopt)\s+"
    r"(?P<subject>[a-z0-9][a-z0-9\s_-]{2,100})"
)
_POSITIVE_USE_RE = re.compile(
    r"\b(?:use|prefer|choose|adopt)\s+"
    r"(?P<subject>[a-z0-9][a-z0-9\s_-]{2,100})"
)
_STATE_RE = re.compile(
    r"\b(?P<subject>[a-z0-9][a-z0-9\s_-]{2,80}?)\s+"
    r"(?:(?:is|are|should\s+be|must\s+be|stays|remains)\s+)?"
    r"(?P<state>enabled|disabled|active|stale)\b"
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
}
_STATE_POLARITY = {
    "enabled": "positive",
    "active": "positive",
    "disabled": "negative",
    "stale": "negative",
}


@dataclass(frozen=True)
class NearDuplicateSignal:
    """A high-similarity text match that is not necessarily byte-identical."""

    signature: str
    score: float
    confidence: float


@dataclass(frozen=True)
class OppositeClaimSignal:
    """A simple opposite-claim match between two similar subjects."""

    match_reason: str
    confidence: float
    left_subject: str
    right_subject: str
    left_claim: str
    right_claim: str

    def evidence(self) -> dict[str, str]:
        return {
            "left_subject": self.left_subject,
            "right_subject": self.right_subject,
            "left_claim": self.left_claim,
            "right_claim": self.right_claim,
        }


@dataclass(frozen=True)
class _Claim:
    kind: str
    polarity: str
    subject: str
    subject_tokens: tuple[str, ...]
    text: str


def near_duplicate_text(
    left: str,
    right: str,
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> Optional[NearDuplicateSignal]:
    """Return a conservative near-duplicate signal for token-equivalent text."""

    left_tokens = text_tokens(left)
    right_tokens = text_tokens(right)
    if len(left_tokens) < 5 or len(right_tokens) < 5:
        return None

    score = token_similarity(left_tokens, right_tokens)
    if score < threshold:
        return None
    return NearDuplicateSignal(
        signature=f"token-jaccard:{_token_digest(left_tokens)}:{_token_digest(right_tokens)}",
        score=round(score, 3),
        confidence=0.86 if score >= 0.98 else 0.8,
    )


def detect_opposite_claim(left: str, right: str) -> Optional[OppositeClaimSignal]:
    """Detect only obvious opposite boolean/status claims on similar subjects."""

    best: Optional[OppositeClaimSignal] = None
    for left_claim in _claims(left):
        for right_claim in _claims(right):
            if left_claim.kind != right_claim.kind:
                continue
            if left_claim.polarity == right_claim.polarity:
                continue
            if _subject_similarity(left_claim.subject_tokens, right_claim.subject_tokens) < CONTRADICTION_SUBJECT_THRESHOLD:
                continue
            signal = _opposite_claim_signal(left_claim, right_claim)
            if best is None or signal.confidence > best.confidence:
                best = signal
    return best


def text_tokens(text: str) -> tuple[str, ...]:
    """Normalize text to lowercase alphanumeric tokens."""

    return tuple(_TOKEN_RE.findall(text.casefold()))


def token_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """Return deterministic Jaccard similarity for normalized token sets."""

    if tuple(left) == tuple(right):
        return 1.0
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _claims(text: str) -> tuple[_Claim, ...]:
    normalized = " ".join(text.casefold().split())
    claims: list[_Claim] = []
    for match in _NEGATED_USE_RE.finditer(normalized):
        claims.append(_claim("use", "negative", match.group("subject"), match.group(0)))
    for match in _POSITIVE_USE_RE.finditer(normalized):
        prefix = normalized[max(0, match.start() - 12): match.start()]
        if "do not" in prefix or "don't" in prefix or "never" in prefix:
            continue
        claims.append(_claim("use", "positive", match.group("subject"), match.group(0)))
    for match in _STATE_RE.finditer(normalized):
        state = match.group("state")
        claims.append(_claim(f"state:{_state_pair(state)}", _STATE_POLARITY[state], match.group("subject"), match.group(0)))
    return tuple(claim for claim in claims if len(claim.subject_tokens) >= 2)


def _claim(kind: str, polarity: str, subject: str, text: str) -> _Claim:
    tokens = _subject_tokens(subject)
    return _Claim(
        kind=kind,
        polarity=polarity,
        subject=" ".join(tokens),
        subject_tokens=tokens,
        text=text.strip(),
    )


def _subject_tokens(subject: str) -> tuple[str, ...]:
    tokens = [token for token in text_tokens(subject) if token not in _STOPWORDS]
    return tuple(tokens[-8:])


def _state_pair(state: str) -> str:
    if state in {"enabled", "disabled"}:
        return "enabled_disabled"
    return "active_stale"


def _subject_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    if left_set == right_set:
        return 1.0
    overlap = len(left_set & right_set)
    return overlap / max(len(left_set), len(right_set))


def _opposite_claim_signal(left: _Claim, right: _Claim) -> OppositeClaimSignal:
    reason = "opposite_claim:use_vs_do_not_use" if left.kind == "use" else f"opposite_claim:{left.kind.split(':', 1)[1]}"
    confidence = 0.86 if left.kind == "use" else 0.82
    return OppositeClaimSignal(
        match_reason=reason,
        confidence=confidence,
        left_subject=left.subject,
        right_subject=right.subject,
        left_claim=left.text,
        right_claim=right.text,
    )


def _token_digest(tokens: Sequence[str]) -> str:
    digest = hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()
    return digest[:16]


__all__ = [
    "NearDuplicateSignal",
    "OppositeClaimSignal",
    "detect_opposite_claim",
    "near_duplicate_text",
    "text_tokens",
    "token_similarity",
]
