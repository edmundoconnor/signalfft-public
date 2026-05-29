"""Lexicon-based polarity scorer for financial text.

Pure functions with no I/O, no side effects, no external dependencies.
Scores text on a [-1.0, +1.0] scale: negative = bearish, positive = bullish.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Keyword dictionaries (frozensets for immutability)
# ---------------------------------------------------------------------------

POSITIVE_TERMS: frozenset[str] = frozenset({
    # Single words
    "beat", "exceeded", "growth", "approved", "awarded", "raised",
    "accelerated", "expanded", "outperformed", "upgraded", "surpassed",
    "robust", "exceptional", "breakthrough", "innovative",
    # Multi-word phrases
    "record revenue", "strong demand", "margin expansion",
    "increased guidance", "above expectations", "ahead of schedule",
    "new contract", "strategic acquisition", "dividend increase",
    "share buyback", "cost savings", "efficiency gains",
    "market share gains", "favorable ruling", "patent granted",
})

NEGATIVE_TERMS: frozenset[str] = frozenset({
    # Single words
    "decline", "default", "impairment", "restatement", "resignation",
    "terminated", "litigation", "downgrade", "withdrawal", "deteriorated",
    "missed", "shortfall", "loss", "writedown", "restructuring", "layoff",
    "recall", "investigation", "subpoena", "bankruptcy",
    # Multi-word phrases
    "going concern", "material weakness", "covenant breach", "debt default",
    "guidance reduced", "below expectations", "margin compression",
    "market share loss", "adverse ruling", "regulatory action",
    "supply disruption", "delayed",
})

# ---------------------------------------------------------------------------
# Precompiled regex patterns (built once at module load)
# ---------------------------------------------------------------------------

def _build_pattern(terms: frozenset[str]) -> re.Pattern:
    """Build a single regex that matches any term with word boundaries.

    Multi-word phrases are matched as-is. Terms are sorted longest-first
    so that "debt default" is tried before "default".
    """
    sorted_terms = sorted(terms, key=len, reverse=True)
    escaped = [re.escape(term) for term in sorted_terms]
    joined = "|".join(escaped)
    return re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)


_POSITIVE_RE = _build_pattern(POSITIVE_TERMS)
_NEGATIVE_RE = _build_pattern(NEGATIVE_TERMS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_polarity(text: str) -> float:
    """Score text polarity on [-1.0, +1.0]. Pure, deterministic, no I/O.

    Formula: (positive_count - negative_count) / (positive_count + negative_count + 1)
    """
    if not text or not text.strip():
        return 0.0

    positive_count = len(_POSITIVE_RE.findall(text))
    negative_count = len(_NEGATIVE_RE.findall(text))

    return (positive_count - negative_count) / (positive_count + negative_count + 1)


def score_polarity_detailed(text: str) -> dict:
    """Score text polarity and return detailed match information.

    Returns:
        dict with keys: polarity_score, positive_count, negative_count,
        positive_matches, negative_matches, total_words
    """
    if not text or not text.strip():
        return {
            "polarity_score": 0.0,
            "positive_count": 0,
            "negative_count": 0,
            "positive_matches": [],
            "negative_matches": [],
            "total_words": 0,
        }

    positive_matches = [m.lower() for m in _POSITIVE_RE.findall(text)]
    negative_matches = [m.lower() for m in _NEGATIVE_RE.findall(text)]

    positive_count = len(positive_matches)
    negative_count = len(negative_matches)
    polarity_score = (positive_count - negative_count) / (positive_count + negative_count + 1)

    total_words = len(text.split())

    return {
        "polarity_score": polarity_score,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "positive_matches": positive_matches,
        "negative_matches": negative_matches,
        "total_words": total_words,
    }
