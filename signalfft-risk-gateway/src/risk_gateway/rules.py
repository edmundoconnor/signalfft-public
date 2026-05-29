"""
Deterministic risk rule engine. All functions are pure — no I/O, no side effects.
These enforce position limits, exposure caps, and candidate rate limits.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    min_signal_score: float = 0.05
    max_position_per_entity: float = 10000.0
    max_total_exposure: float = 100000.0
    max_candidates_per_entity: int = 3
    max_candidates_per_window: int = 10


DEFAULT_RISK_CONFIG = RiskConfig()


@dataclass
class RiskCheckResult:
    passed: bool
    check_name: str
    rejection_reason: str | None = None


def check_min_score(score: float, config: RiskConfig = DEFAULT_RISK_CONFIG) -> RiskCheckResult:
    """Signal score must be >= min_signal_score."""
    passed = score >= config.min_signal_score
    return RiskCheckResult(
        passed=passed,
        check_name="min_score",
        rejection_reason=f"Score {score:.4f} below minimum {config.min_signal_score}" if not passed else None,
    )


def check_entity_position_limit(
    current_entity_exposure: float,
    proposed_amount: float,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> RiskCheckResult:
    """Current entity exposure + proposed must not exceed max_position_per_entity."""
    total = current_entity_exposure + proposed_amount
    passed = total <= config.max_position_per_entity
    return RiskCheckResult(
        passed=passed,
        check_name="entity_position",
        rejection_reason=(
            f"Entity exposure {current_entity_exposure} + proposed {proposed_amount} "
            f"= {total} exceeds max {config.max_position_per_entity}"
        ) if not passed else None,
    )


def check_total_exposure(
    current_total_exposure: float,
    proposed_amount: float,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> RiskCheckResult:
    """Current total exposure + proposed must not exceed max_total_exposure."""
    total = current_total_exposure + proposed_amount
    passed = total <= config.max_total_exposure
    return RiskCheckResult(
        passed=passed,
        check_name="total_exposure",
        rejection_reason=(
            f"Total exposure {current_total_exposure} + proposed {proposed_amount} "
            f"= {total} exceeds max {config.max_total_exposure}"
        ) if not passed else None,
    )


def check_entity_candidate_count(
    current_candidate_count: int,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> RiskCheckResult:
    """Entity must have fewer than max_candidates_per_entity active candidates."""
    passed = current_candidate_count < config.max_candidates_per_entity
    return RiskCheckResult(
        passed=passed,
        check_name="entity_candidates",
        rejection_reason=(
            f"Entity has {current_candidate_count} active candidates, "
            f"max is {config.max_candidates_per_entity}"
        ) if not passed else None,
    )


def check_window_candidate_count(
    current_window_count: int,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> RiskCheckResult:
    """Must not exceed max_candidates_per_window in current scoring window."""
    passed = current_window_count < config.max_candidates_per_window
    return RiskCheckResult(
        passed=passed,
        check_name="window_candidates",
        rejection_reason=(
            f"Window has {current_window_count} candidates, "
            f"max is {config.max_candidates_per_window}"
        ) if not passed else None,
    )


def run_all_checks(
    score: float,
    current_entity_exposure: float,
    current_total_exposure: float,
    current_entity_candidate_count: int,
    current_window_candidate_count: int,
    proposed_amount: float = 1000.0,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> tuple[bool, str | None, list[str]]:
    """
    Run all risk checks in order. Fail fast on first rejection.

    Returns: (passed, rejection_reason, checks_performed)
    """
    checks = [
        lambda: check_min_score(score, config),
        lambda: check_entity_position_limit(current_entity_exposure, proposed_amount, config),
        lambda: check_total_exposure(current_total_exposure, proposed_amount, config),
        lambda: check_entity_candidate_count(current_entity_candidate_count, config),
        lambda: check_window_candidate_count(current_window_candidate_count, config),
    ]

    checks_performed: list[str] = []
    for check_fn in checks:
        result = check_fn()
        checks_performed.append(result.check_name)
        if not result.passed:
            return False, result.rejection_reason, checks_performed

    return True, None, checks_performed
