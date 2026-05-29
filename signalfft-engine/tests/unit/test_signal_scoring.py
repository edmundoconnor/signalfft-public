"""Comprehensive tests for the signal scoring pure function."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st, assume

from engine.signal_scoring.scorer import (
    compute_signal_score,
    decompose_score,
    validate_components,
    DEFAULT_WEIGHTS,
    COMPONENT_KEYS,
)
from signalfft_common.models import WeightConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_components(**overrides: float) -> dict[str, float]:
    """Return a complete components dict with defaults of 0.5, plus overrides."""
    base = {k: 0.5 for k in COMPONENT_KEYS}
    base.update(overrides)
    return base


def _zero_components() -> dict[str, float]:
    """Return components dict with all values set to 0.0."""
    return {k: 0.0 for k in COMPONENT_KEYS}


def _one_components() -> dict[str, float]:
    """Return components dict with all values set to 1.0."""
    return {k: 1.0 for k in COMPONENT_KEYS}


# Hypothesis strategy for a valid component value in [0, 1]
component_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Hypothesis strategy for a full components dict
components_strategy = st.fixed_dictionaries(
    {k: component_value for k in COMPONENT_KEYS}
)

# Hypothesis strategy for a WeightConfig with positive weights
weight_strategy = st.builds(
    WeightConfig,
    wn=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    wv=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    wc=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    ws=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    we=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    wh=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    wp=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)


# ===========================================================================
# Basic scoring tests
# ===========================================================================


class TestBasicScoring:
    """Tests for core compute_signal_score behavior."""

    def test_all_zeros_returns_zero(self):
        """All component values at 0.0 should produce a score of 0.0."""
        components = _zero_components()
        assert compute_signal_score(components) == 0.0

    def test_all_ones_no_penalty(self):
        """All components at 1.0 except noise_penalty at 0.0.

        Expected: wn + wv + wc + ws + we + wh - 0 = 0.20 + 0.15 + 0.15 + 0.20 + 0.10 + 0.10 = 0.90
        """
        components = _one_components()
        components["noise_penalty"] = 0.0
        score = compute_signal_score(components)
        assert score == pytest.approx(0.90, abs=1e-9)

    def test_all_ones_with_penalty(self):
        """All components at 1.0 including noise_penalty.

        Expected raw: 0.20 + 0.15 + 0.15 + 0.20 + 0.10 + 0.10 - 0.10 = 0.80
        """
        components = _one_components()
        score = compute_signal_score(components)
        assert score == pytest.approx(0.80, abs=1e-9)

    def test_default_weights_used(self):
        """Calling without weights argument should use DEFAULT_WEIGHTS."""
        components = _full_components()
        score_default = compute_signal_score(components)
        score_explicit = compute_signal_score(components, weights=DEFAULT_WEIGHTS)
        assert score_default == score_explicit

    def test_custom_weights(self):
        """Custom WeightConfig should produce a different result than defaults."""
        components = _full_components()
        custom = WeightConfig(wn=1.0, wv=0.0, wc=0.0, ws=0.0, we=0.0, wh=0.0, wp=0.0)
        score = compute_signal_score(components, weights=custom)
        # Only novelty contributes: 0.5 * 1.0 = 0.5
        assert score == pytest.approx(0.5, abs=1e-9)


# ===========================================================================
# Clamping tests
# ===========================================================================


class TestClamping:
    """Tests for score clamping to [0.0, 1.0]."""

    def test_score_clamped_to_zero(self):
        """Heavy noise penalty with no positive components should clamp to 0.0."""
        components = _zero_components()
        components["noise_penalty"] = 1.0
        score = compute_signal_score(components)
        assert score == 0.0

    def test_score_clamped_to_one(self):
        """If raw score exceeds 1.0, it should be clamped to 1.0."""
        # Use weights that sum to > 1.0 for positive components
        heavy_weights = WeightConfig(wn=0.5, wv=0.5, wc=0.5, ws=0.5, we=0.5, wh=0.5, wp=0.0)
        components = _one_components()
        components["noise_penalty"] = 0.0
        score = compute_signal_score(components, weights=heavy_weights)
        assert score == 1.0

    def test_score_between_zero_and_one(self):
        """Normal inputs should produce a score strictly within [0, 1]."""
        components = _full_components()
        score = compute_signal_score(components)
        assert 0.0 <= score <= 1.0


# ===========================================================================
# Missing / extra component tests
# ===========================================================================


class TestMissingExtraComponents:
    """Tests for handling of incomplete or extra component dictionaries."""

    def test_missing_components_default_zero(self):
        """Empty dict should produce score of 0.0 (all components default to 0.0)."""
        assert compute_signal_score({}) == 0.0

    def test_partial_components(self):
        """Only providing some keys should use 0.0 for missing ones."""
        components = {"novelty": 1.0}
        score = compute_signal_score(components)
        # Only novelty contributes: 1.0 * 0.20 = 0.20
        assert score == pytest.approx(0.20, abs=1e-9)

    def test_extra_components_ignored(self):
        """Unknown keys in the dict should not affect the score."""
        components = _full_components()
        components["unknown_key"] = 999.0
        components["another_fake"] = -100.0
        score_with_extra = compute_signal_score(components)
        score_without = compute_signal_score(_full_components())
        assert score_with_extra == score_without


# ===========================================================================
# Determinism tests
# ===========================================================================


class TestDeterminism:
    """Tests ensuring identical inputs always produce identical outputs."""

    def test_deterministic_same_inputs(self):
        """Same inputs must always produce the same output."""
        components = _full_components(novelty=0.8, noise_penalty=0.3)
        score1 = compute_signal_score(components)
        score2 = compute_signal_score(components)
        assert score1 == score2

    def test_deterministic_across_calls(self):
        """Multiple calls with identical inputs should all match."""
        components = _full_components(velocity=0.9, cross_source=0.1)
        scores = [compute_signal_score(components) for _ in range(100)]
        assert all(s == scores[0] for s in scores)


# ===========================================================================
# Decompose tests
# ===========================================================================


class TestDecompose:
    """Tests for the decompose_score function."""

    def test_decompose_returns_all_keys(self):
        """Decompose should return exactly 7 component keys."""
        components = _full_components()
        result = decompose_score(components)
        assert set(result.keys()) == set(COMPONENT_KEYS)

    def test_decompose_matches_score(self):
        """Sum of decomposed values should equal the unclamped raw score.

        Since we clamp the final score, we compare against the raw (pre-clamp) value.
        """
        components = _full_components()
        decomposed = decompose_score(components)
        raw_sum = sum(decomposed.values())
        # The final score is clamped, but for mid-range components, no clamping occurs
        score = compute_signal_score(components)
        assert raw_sum == pytest.approx(score, abs=1e-9)

    def test_decompose_noise_is_negative(self):
        """The noise_penalty contribution should be negative (or zero)."""
        components = _full_components(noise_penalty=0.8)
        decomposed = decompose_score(components)
        assert decomposed["noise_penalty"] <= 0.0

    def test_decompose_noise_zero_when_penalty_zero(self):
        """When noise_penalty is 0, its decomposed contribution should be 0."""
        components = _full_components(noise_penalty=0.0)
        decomposed = decompose_score(components)
        assert decomposed["noise_penalty"] == 0.0

    def test_decompose_with_custom_weights(self):
        """Decompose should respect custom weights."""
        components = {"novelty": 0.5, "velocity": 0.5}
        custom = WeightConfig(wn=0.4, wv=0.6, wc=0.0, ws=0.0, we=0.0, wh=0.0, wp=0.0)
        decomposed = decompose_score(components, weights=custom)
        assert decomposed["novelty"] == pytest.approx(0.2, abs=1e-9)
        assert decomposed["velocity"] == pytest.approx(0.3, abs=1e-9)


# ===========================================================================
# Validate tests
# ===========================================================================


class TestValidate:
    """Tests for the validate_components function."""

    def test_validate_no_warnings_complete(self):
        """A complete, valid components dict should produce no warnings."""
        components = _full_components()
        warnings = validate_components(components)
        assert warnings == []

    def test_validate_missing_keys(self):
        """Missing keys should produce warnings."""
        components = {"novelty": 0.5}
        warnings = validate_components(components)
        missing_warnings = [w for w in warnings if "Missing component" in w]
        # Should have 6 missing keys (all except novelty)
        assert len(missing_warnings) == 6

    def test_validate_out_of_range(self):
        """Values outside [0, 1] should produce warnings."""
        components = _full_components(novelty=1.5, velocity=-0.1)
        warnings = validate_components(components)
        range_warnings = [w for w in warnings if "Out of range" in w]
        assert len(range_warnings) == 2

    def test_validate_unknown_keys(self):
        """Extra/unknown keys should produce warnings."""
        components = _full_components()
        components["bogus_key"] = 0.5
        warnings = validate_components(components)
        unknown_warnings = [w for w in warnings if "Unknown component" in w]
        assert len(unknown_warnings) == 1
        assert "bogus_key" in unknown_warnings[0]

    def test_validate_non_numeric_value(self):
        """Non-numeric values should produce warnings."""
        components = _full_components()
        components["novelty"] = "not_a_number"
        warnings = validate_components(components)
        non_numeric_warnings = [w for w in warnings if "Non-numeric" in w]
        assert len(non_numeric_warnings) == 1

    def test_validate_empty_dict(self):
        """Empty dict should produce 7 missing-key warnings."""
        warnings = validate_components({})
        missing_warnings = [w for w in warnings if "Missing component" in w]
        assert len(missing_warnings) == 7

    def test_validate_boundary_values_ok(self):
        """Values at exactly 0.0 and 1.0 should not produce range warnings."""
        components = {k: 0.0 for k in COMPONENT_KEYS}
        components["novelty"] = 1.0
        warnings = validate_components(components)
        assert warnings == []


# ===========================================================================
# Hypothesis property-based tests
# ===========================================================================


class TestPropertyBased:
    """Property-based tests using hypothesis."""

    @given(components=components_strategy, weights=weight_strategy)
    def test_score_always_in_range(self, components: dict, weights: WeightConfig):
        """For any valid components and weights, score must be in [0.0, 1.0]."""
        score = compute_signal_score(components, weights=weights)
        assert 0.0 <= score <= 1.0

    @given(
        components=components_strategy,
        increase=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    def test_higher_components_higher_score(self, components: dict, increase: float):
        """Increasing any positive component (not noise_penalty) should not decrease the score."""
        positive_keys = [k for k in COMPONENT_KEYS if k != "noise_penalty"]
        for key in positive_keys:
            original_val = components[key]
            # Only test if there is room to increase
            if original_val + increase <= 1.0:
                score_before = compute_signal_score(components)
                boosted = dict(components)
                boosted[key] = original_val + increase
                score_after = compute_signal_score(boosted)
                assert score_after >= score_before - 1e-12  # Tolerance for float arithmetic

    @given(
        components=components_strategy,
        increase=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    def test_higher_penalty_lower_score(self, components: dict, increase: float):
        """Increasing noise_penalty should not increase the score."""
        original_penalty = components["noise_penalty"]
        if original_penalty + increase <= 1.0:
            score_before = compute_signal_score(components)
            penalized = dict(components)
            penalized["noise_penalty"] = original_penalty + increase
            score_after = compute_signal_score(penalized)
            assert score_after <= score_before + 1e-12  # Tolerance for float arithmetic

    @given(components=components_strategy)
    def test_decompose_sum_matches_raw(self, components: dict):
        """The sum of decomposed values should match the raw (pre-clamp) score."""
        decomposed = decompose_score(components)
        raw_sum = sum(decomposed.values())
        # Manually compute expected raw
        w = DEFAULT_WEIGHTS
        expected_raw = (
            components["novelty"] * w.wn
            + components["velocity"] * w.wv
            + components["cross_source"] * w.wc
            + components["semantic_impact"] * w.ws
            + components["entity_sensitivity"] * w.we
            + components["historical_pattern"] * w.wh
            - components["noise_penalty"] * w.wp
        )
        assert raw_sum == pytest.approx(expected_raw, abs=1e-9)

    @given(components=components_strategy)
    def test_validate_complete_no_warnings(self, components: dict):
        """A complete dict of valid [0,1] floats should produce no warnings."""
        warnings = validate_components(components)
        assert warnings == []


# ===========================================================================
# Edge case tests
# ===========================================================================


class TestEdgeCases:
    """Additional edge case tests for robustness."""

    def test_only_noise_penalty(self):
        """Only providing noise_penalty should yield 0.0 (clamped from negative)."""
        components = {"noise_penalty": 1.0}
        score = compute_signal_score(components)
        assert score == 0.0

    def test_weight_config_is_frozen(self):
        """WeightConfig should be immutable (frozen dataclass)."""
        with pytest.raises(AttributeError):
            DEFAULT_WEIGHTS.wn = 0.99

    def test_integer_component_values(self):
        """Integer values (0, 1) should work just like floats."""
        components = {k: 1 for k in COMPONENT_KEYS}
        components["noise_penalty"] = 0
        score = compute_signal_score(components)
        assert score == pytest.approx(0.90, abs=1e-9)

    def test_component_keys_tuple(self):
        """COMPONENT_KEYS should contain exactly 7 entries."""
        assert len(COMPONENT_KEYS) == 7
        assert isinstance(COMPONENT_KEYS, tuple)

    def test_default_weights_sum_to_one(self):
        """Positive weights (excluding penalty) should sum to 0.90, total including penalty to 1.0."""
        w = DEFAULT_WEIGHTS
        positive_sum = w.wn + w.wv + w.wc + w.ws + w.we + w.wh
        assert positive_sum == pytest.approx(0.90, abs=1e-9)
        total = positive_sum + w.wp
        assert total == pytest.approx(1.00, abs=1e-9)
