"""Aggregate direction computation — combines lexicon and Claude layers.

Pure functions with no I/O, no side effects, no external dependencies.
Receives pre-computed inputs and returns a direction score in [-1.0, +1.0].
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Weight configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DirectionWeights:
    """Weights for blending lexicon and Claude directional signals."""

    lexicon_weight: float = 0.3
    claude_weight: float = 0.7


DEFAULT_WEIGHTS = DirectionWeights()

# ---------------------------------------------------------------------------
# Claude direction mapping
# ---------------------------------------------------------------------------

_DIRECTION_MAP: dict[str, float] = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
}

# Dead-zone threshold: scores with abs value below this are labelled neutral.
_NEUTRAL_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_direction_score(
    lexicon_polarity: float,
    claude_direction: str | None,
    claude_confidence: float | None,
    weights: DirectionWeights | None = None,
) -> float:
    """Compute a blended direction score from lexicon and Claude layers.

    Args:
        lexicon_polarity: Polarity from score_polarity(), range [-1.0, +1.0].
        claude_direction: "bullish", "bearish", "neutral", or None.
        claude_confidence: 0.0-1.0, or None if Claude unavailable.
        weights: Optional weight override. Uses DEFAULT_WEIGHTS if not provided.

    Returns:
        Direction score clamped to [-1.0, +1.0].
        Negative = bearish, positive = bullish, 0.0 = neutral.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    claude_available = claude_direction is not None and claude_confidence is not None

    if claude_available:
        direction_value = _DIRECTION_MAP.get(claude_direction, 0.0)  # type: ignore[arg-type]
        claude_numeric = direction_value * claude_confidence  # type: ignore[operator]
        raw = (lexicon_polarity * weights.lexicon_weight) + (claude_numeric * weights.claude_weight)
    else:
        raw = lexicon_polarity

    return max(-1.0, min(1.0, raw))


def compute_direction_detailed(
    lexicon_polarity: float,
    claude_direction: str | None,
    claude_confidence: float | None,
    weights: DirectionWeights | None = None,
) -> dict:
    """Compute direction score with full breakdown for auditability.

    Returns:
        Dict with keys: direction_score, direction_label, lexicon_polarity,
        lexicon_contribution, claude_direction, claude_confidence,
        claude_numeric, claude_contribution, weights_used, claude_available.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    claude_available = claude_direction is not None and claude_confidence is not None

    if claude_available:
        direction_value = _DIRECTION_MAP.get(claude_direction, 0.0)  # type: ignore[arg-type]
        claude_numeric = direction_value * claude_confidence  # type: ignore[operator]
        lexicon_contribution = lexicon_polarity * weights.lexicon_weight
        claude_contribution = claude_numeric * weights.claude_weight
        raw = lexicon_contribution + claude_contribution
    else:
        claude_numeric = 0.0
        lexicon_contribution = lexicon_polarity
        claude_contribution = 0.0
        raw = lexicon_polarity

    direction_score = max(-1.0, min(1.0, raw))

    if abs(direction_score) < _NEUTRAL_THRESHOLD:
        direction_label = "neutral"
    elif direction_score > 0:
        direction_label = "bullish"
    else:
        direction_label = "bearish"

    return {
        "direction_score": direction_score,
        "direction_label": direction_label,
        "lexicon_polarity": lexicon_polarity,
        "lexicon_contribution": lexicon_contribution,
        "claude_direction": claude_direction,
        "claude_confidence": claude_confidence,
        "claude_numeric": claude_numeric,
        "claude_contribution": claude_contribution,
        "weights_used": {
            "lexicon": weights.lexicon_weight,
            "claude": weights.claude_weight,
        },
        "claude_available": claude_available,
    }
