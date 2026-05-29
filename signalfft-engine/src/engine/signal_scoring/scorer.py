"""Pure signal scoring function — no I/O, deterministic."""

from __future__ import annotations

from signalfft_common.models import WeightConfig

COMPONENT_KEYS = (
    "novelty",
    "velocity",
    "cross_source",
    "semantic_impact",
    "entity_sensitivity",
    "historical_pattern",
    "noise_penalty",
)

DEFAULT_WEIGHTS = WeightConfig(
    wn=0.20,  # Novelty
    wv=0.15,  # Velocity
    wc=0.15,  # CrossSourceCorrelation
    ws=0.20,  # SemanticImpact
    we=0.10,  # EntitySensitivity
    wh=0.10,  # HistoricalPattern
    wp=0.10,  # NoisePenalty
)


def compute_signal_score(
    components: dict[str, float],
    weights: WeightConfig | None = None,
) -> float:
    """Compute a deterministic signal score from feature components and weights.

    Args:
        components: dict mapping component names to float values (each 0.0-1.0).
            Required keys: novelty, velocity, cross_source, semantic_impact,
            entity_sensitivity, historical_pattern, noise_penalty.
            Missing keys default to 0.0.
        weights: Optional WeightConfig. Uses DEFAULT_WEIGHTS if not provided.

    Returns:
        A float clamped to [0.0, 1.0].
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    n = components.get("novelty", 0.0)
    v = components.get("velocity", 0.0)
    c = components.get("cross_source", 0.0)
    s = components.get("semantic_impact", 0.0)
    e = components.get("entity_sensitivity", 0.0)
    h = components.get("historical_pattern", 0.0)
    p = components.get("noise_penalty", 0.0)

    raw = (
        n * weights.wn
        + v * weights.wv
        + c * weights.wc
        + s * weights.ws
        + e * weights.we
        + h * weights.wh
        - p * weights.wp
    )

    return max(0.0, min(1.0, raw))


def decompose_score(
    components: dict[str, float],
    weights: WeightConfig | None = None,
) -> dict[str, float]:
    """Return per-component weighted contributions (before clamping).

    Useful for auditability — shows exactly how much each factor contributed.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    return {
        "novelty": components.get("novelty", 0.0) * weights.wn,
        "velocity": components.get("velocity", 0.0) * weights.wv,
        "cross_source": components.get("cross_source", 0.0) * weights.wc,
        "semantic_impact": components.get("semantic_impact", 0.0) * weights.ws,
        "entity_sensitivity": components.get("entity_sensitivity", 0.0) * weights.we,
        "historical_pattern": components.get("historical_pattern", 0.0) * weights.wh,
        "noise_penalty": -(components.get("noise_penalty", 0.0) * weights.wp),
    }


def validate_components(components: dict[str, float]) -> list[str]:
    """Return list of warnings for invalid component values.

    Checks:
    - Missing keys (returns warning but doesn't fail)
    - Values outside [0.0, 1.0] range
    - Unknown keys
    """
    warnings = []

    for key in COMPONENT_KEYS:
        if key not in components:
            warnings.append(f"Missing component: {key} (defaulting to 0.0)")

    for key, value in components.items():
        if key not in COMPONENT_KEYS:
            warnings.append(f"Unknown component: {key}")
        elif not isinstance(value, (int, float)):
            warnings.append(f"Non-numeric value for {key}: {value}")
        elif value < 0.0 or value > 1.0:
            warnings.append(f"Out of range [0,1] for {key}: {value}")

    return warnings
