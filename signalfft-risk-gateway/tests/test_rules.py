"""Tests for deterministic risk rule engine."""
import pytest

from risk_gateway.rules import (
    DEFAULT_RISK_CONFIG,
    RiskCheckResult,
    RiskConfig,
    check_entity_candidate_count,
    check_entity_position_limit,
    check_min_score,
    check_total_exposure,
    check_window_candidate_count,
    run_all_checks,
)


def test_check_min_score_pass():
    result = check_min_score(0.10)
    assert result.passed is True
    assert result.check_name == "min_score"
    assert result.rejection_reason is None


def test_check_min_score_fail():
    result = check_min_score(0.01)
    assert result.passed is False
    assert result.check_name == "min_score"
    assert "below minimum" in result.rejection_reason


def test_check_min_score_exact_threshold():
    result = check_min_score(0.05)
    assert result.passed is True


def test_check_entity_position_limit_pass():
    result = check_entity_position_limit(5000.0, 1000.0)
    assert result.passed is True
    assert result.check_name == "entity_position"


def test_check_entity_position_limit_fail():
    result = check_entity_position_limit(9500.0, 1000.0)
    assert result.passed is False
    assert "exceeds max" in result.rejection_reason


def test_check_total_exposure_pass():
    result = check_total_exposure(50000.0, 1000.0)
    assert result.passed is True
    assert result.check_name == "total_exposure"


def test_check_total_exposure_fail():
    result = check_total_exposure(99500.0, 1000.0)
    assert result.passed is False
    assert "exceeds max" in result.rejection_reason


def test_check_entity_candidate_count_pass():
    result = check_entity_candidate_count(2)
    assert result.passed is True
    assert result.check_name == "entity_candidates"


def test_check_entity_candidate_count_fail():
    result = check_entity_candidate_count(3)
    assert result.passed is False
    assert "max is 3" in result.rejection_reason


def test_check_window_candidate_count_pass():
    result = check_window_candidate_count(5)
    assert result.passed is True
    assert result.check_name == "window_candidates"


def test_check_window_candidate_count_fail():
    result = check_window_candidate_count(10)
    assert result.passed is False
    assert "max is 10" in result.rejection_reason


def test_run_all_checks_all_pass():
    passed, reason, checks = run_all_checks(
        score=0.10,
        current_entity_exposure=5000.0,
        current_total_exposure=50000.0,
        current_entity_candidate_count=1,
        current_window_candidate_count=3,
    )
    assert passed is True
    assert reason is None
    assert len(checks) == 5


def test_run_all_checks_fail_fast_on_score():
    passed, reason, checks = run_all_checks(
        score=0.01,
        current_entity_exposure=0.0,
        current_total_exposure=0.0,
        current_entity_candidate_count=0,
        current_window_candidate_count=0,
    )
    assert passed is False
    assert "below minimum" in reason
    assert checks == ["min_score"]


def test_run_all_checks_fail_fast_on_entity_position():
    passed, reason, checks = run_all_checks(
        score=0.10,
        current_entity_exposure=9500.0,
        current_total_exposure=50000.0,
        current_entity_candidate_count=0,
        current_window_candidate_count=0,
    )
    assert passed is False
    assert "exceeds max" in reason
    assert checks == ["min_score", "entity_position"]


def test_custom_config():
    config = RiskConfig(min_signal_score=0.20, max_position_per_entity=5000.0)
    result = check_min_score(0.15, config=config)
    assert result.passed is False

    result = check_entity_position_limit(4500.0, 1000.0, config=config)
    assert result.passed is False


def test_default_config_values():
    assert DEFAULT_RISK_CONFIG.min_signal_score == 0.05
    assert DEFAULT_RISK_CONFIG.max_position_per_entity == 10000.0
    assert DEFAULT_RISK_CONFIG.max_total_exposure == 100000.0
    assert DEFAULT_RISK_CONFIG.max_candidates_per_entity == 3
    assert DEFAULT_RISK_CONFIG.max_candidates_per_window == 10
