"""Fast keyword triage for SEC filings.

Pure functions with no I/O. Scans filing text for high-impact keywords
across 5 categories and returns priority classification. Runs inline
during feature extraction, adding <50ms latency.

Categories:
  - executive_changes: CEO/CFO resignations, appointments, departures
  - financial_distress: bankruptcy, going concern, material weakness
  - legal_regulatory: SEC investigation, subpoena, class action
  - corporate_actions: merger, acquisition, tender offer
  - guidance_changes: withdrew guidance, suspended dividend
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Keyword dictionaries by category (frozensets for immutability)
# ---------------------------------------------------------------------------

EXECUTIVE_CHANGES: frozenset[str] = frozenset({
    "resignation", "resigned", "terminated", "appointed",
    "interim ceo", "interim cfo", "transition",
    "stepped down", "departing", "succession",
})

FINANCIAL_DISTRESS: frozenset[str] = frozenset({
    "default", "bankruptcy", "going concern", "impairment",
    "restatement", "material weakness", "covenant breach",
    "liquidity concern",
    "doubt about ability to continue",
    "unable to meet obligations",
})

LEGAL_REGULATORY: frozenset[str] = frozenset({
    "sec investigation", "subpoena", "class action",
    "consent decree", "enforcement action",
    "doj", "securities fraud", "regulatory proceeding",
    "cease and desist", "wells notice",
})

CORPORATE_ACTIONS: frozenset[str] = frozenset({
    "merger", "acquisition", "divestiture", "spin-off",
    "tender offer", "change of control", "proxy fight",
    "hostile takeover", "buyout", "going private",
})

GUIDANCE_CHANGES: frozenset[str] = frozenset({
    "withdraw guidance", "withdrew guidance",
    "revise guidance", "revised guidance",
    "suspend dividend", "suspended dividend",
    "reduce forecast", "reduced forecast",
    "lowered expectations", "no longer providing guidance",
})

CATEGORIES: dict[str, frozenset[str]] = {
    "executive_changes": EXECUTIVE_CHANGES,
    "financial_distress": FINANCIAL_DISTRESS,
    "legal_regulatory": LEGAL_REGULATORY,
    "corporate_actions": CORPORATE_ACTIONS,
    "guidance_changes": GUIDANCE_CHANGES,
}

# Categories that are always HIGH priority even with a single match
_ALWAYS_HIGH: frozenset[str] = frozenset({
    "financial_distress",
    "executive_changes",
})


# ---------------------------------------------------------------------------
# Precompiled regex patterns (built once at module load)
# ---------------------------------------------------------------------------

def _build_pattern(terms: frozenset[str]) -> re.Pattern:
    """Build a single regex matching any term with word boundaries.

    Terms sorted longest-first so multi-word phrases match before substrings.
    """
    sorted_terms = sorted(terms, key=len, reverse=True)
    escaped = [re.escape(term) for term in sorted_terms]
    joined = "|".join(escaped)
    return re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)


_CATEGORY_PATTERNS: dict[str, re.Pattern] = {
    name: _build_pattern(terms) for name, terms in CATEGORIES.items()
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TriageResult:
    """Result of keyword triage scan."""

    is_high_priority: bool
    matched_categories: list[str] = field(default_factory=list)
    matched_terms: list[dict] = field(default_factory=list)
    category_count: int = 0
    priority_level: str = "NONE"  # NONE, MEDIUM, HIGH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def triage_filing(text: str) -> TriageResult:
    """Scan filing text for high-impact keywords. Pure, deterministic, no I/O.

    Priority rules:
      - 0 categories matched → NONE (is_high_priority=False)
      - 1 category matched → MEDIUM (unless it's financial_distress or
        executive_changes, which are always HIGH)
      - 2+ categories matched → HIGH

    Returns:
        TriageResult with matched categories, terms, and priority level.
    """
    if not text or not text.strip():
        return TriageResult(is_high_priority=False)

    matched_categories: list[str] = []
    matched_terms: list[dict] = []

    for category, pattern in _CATEGORY_PATTERNS.items():
        for match in pattern.finditer(text):
            matched_terms.append({
                "term": match.group().lower(),
                "category": category,
                "position": match.start(),
            })

        # Check if this category had any matches
        category_terms = [t for t in matched_terms if t["category"] == category]
        if category_terms:
            matched_categories.append(category)

    category_count = len(matched_categories)

    if category_count == 0:
        return TriageResult(is_high_priority=False)

    # Priority: 2+ categories → HIGH; 1 always-high category → HIGH; else MEDIUM
    if category_count >= 2:
        priority_level = "HIGH"
    elif matched_categories[0] in _ALWAYS_HIGH:
        priority_level = "HIGH"
    else:
        priority_level = "MEDIUM"

    return TriageResult(
        is_high_priority=True,
        matched_categories=matched_categories,
        matched_terms=matched_terms,
        category_count=category_count,
        priority_level=priority_level,
    )
