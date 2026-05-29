"""Deterministic scoring for semantic delta shifts.

Pure library, no AWS/Claude dependencies.
Loads config from JSON, scores shifts using a max_plus_diminishing aggregation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALID_SHIFT_TYPES = frozenset({
    "risk_escalation", "risk_removal", "tone_shift",
    "guidance_change", "disclosure_addition", "disclosure_removal",
})

VALID_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})


@dataclass(frozen=True, slots=True)
class DeltaScoringConfig:
    """Frozen config loaded from delta_scoring.json."""

    shift_type_base_scores: dict[str, float]
    direction_multipliers: dict[str, float]
    severity_weights: dict[str, float]
    section_weights: dict[str, float]
    diminishing_factor: float
    max_composite_score: float


_config_cache: DeltaScoringConfig | None = None


def _resolve_config_path() -> Path:
    """Resolve path to the scoring config JSON."""
    env_path = os.environ.get("DELTA_SCORING_CONFIG_PATH", "")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        # If it's a directory, look for the file inside
        candidate = p / "delta_scoring.json"
        if candidate.is_file():
            return candidate
        return p

    # Development: resolve relative to this file
    # signalfft-engine/src/engine/ai_edges/semantic_delta/scoring.py
    # -> ../../../../../../signalfft-opus/config/delta_scoring.json
    engine_root = Path(__file__).resolve().parents[4]  # signalfft-engine/
    return engine_root.parent / "signalfft-opus" / "config" / "delta_scoring.json"


def load_config() -> DeltaScoringConfig:
    """Load and cache scoring config from JSON file."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    path = _resolve_config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))

    _config_cache = DeltaScoringConfig(
        shift_type_base_scores=raw["shift_type_base_scores"],
        direction_multipliers=raw["direction_multipliers"],
        severity_weights=raw["severity_weights"],
        section_weights=raw["section_weights"],
        diminishing_factor=raw["aggregation"]["diminishing_factor"],
        max_composite_score=raw["aggregation"]["max_composite_score"],
    )
    return _config_cache


def clear_config_cache() -> None:
    """Clear the cached config (for testing)."""
    global _config_cache
    _config_cache = None


# ---------------------------------------------------------------------------
# DeltaScore result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DeltaScore:
    """Result of deterministic delta scoring."""

    composite_score: float
    dominant_direction: str
    shift_count: int
    top_shift_type: str


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------

def score_delta(
    shifts: list[dict],
    section_name: str,
    config: DeltaScoringConfig | None = None,
) -> DeltaScore:
    """Score a list of semantic shifts for a single section.

    Each shift dict must have: shift_type, severity, direction.

    Returns DeltaScore with composite_score, dominant_direction,
    shift_count, and top_shift_type.
    """
    if config is None:
        config = load_config()

    if not shifts:
        return DeltaScore(
            composite_score=0.0,
            dominant_direction="neutral",
            shift_count=0,
            top_shift_type="",
        )

    section_weight = config.section_weights.get(
        section_name, config.section_weights.get("default", 0.5),
    )

    # Score each individual shift
    scored_shifts: list[tuple[float, str, str]] = []  # (abs_score, direction, shift_type)

    for shift in shifts:
        shift_type = shift.get("shift_type", "")
        severity = shift.get("severity", 1)
        direction = shift.get("direction", "neutral")

        # Validate
        if shift_type not in VALID_SHIFT_TYPES:
            continue
        if direction not in VALID_DIRECTIONS:
            direction = "neutral"
        severity = max(1, min(5, int(severity)))

        base = config.shift_type_base_scores.get(shift_type, 0.0)
        sev_weight = config.severity_weights.get(str(severity), 0.2)
        dir_mult = config.direction_multipliers.get(direction, 0.0)

        raw_score = base * sev_weight * section_weight
        signed_score = raw_score * dir_mult

        scored_shifts.append((abs(signed_score), direction, shift_type))

    if not scored_shifts:
        return DeltaScore(
            composite_score=0.0,
            dominant_direction="neutral",
            shift_count=0,
            top_shift_type="",
        )

    # Sort by absolute score descending
    scored_shifts.sort(key=lambda x: x[0], reverse=True)

    # Max-plus-diminishing aggregation
    composite = scored_shifts[0][0]
    for i, (score, _, _) in enumerate(scored_shifts[1:], start=1):
        composite += score * (config.diminishing_factor ** i)

    composite = min(composite, config.max_composite_score)

    # Dominant direction: score-weighted majority vote
    direction_scores: dict[str, float] = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
    for score, direction, _ in scored_shifts:
        direction_scores[direction] += score

    dominant_direction = max(direction_scores, key=lambda d: direction_scores[d])
    # If all zero, default to neutral
    if all(v == 0.0 for v in direction_scores.values()):
        dominant_direction = "neutral"

    return DeltaScore(
        composite_score=round(composite, 6),
        dominant_direction=dominant_direction,
        shift_count=len(scored_shifts),
        top_shift_type=scored_shifts[0][2],
    )
