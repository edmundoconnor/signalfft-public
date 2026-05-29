"""Tests for trade candidate generator."""
from risk_gateway.candidate_generator import generate_candidates


def test_generate_candidates_basic():
    signals = [
        {"signal_id": "S1", "entity_id": "AAPL", "score": 0.20},
        {"signal_id": "S2", "entity_id": "MSFT", "score": 0.15},
    ]
    candidates = generate_candidates(signals)
    assert len(candidates) == 2
    assert candidates[0]["score"] == 0.20
    assert candidates[1]["score"] == 0.15
    assert all(c["risk_status"] == "PENDING" for c in candidates)
    assert all(c["risk_rejection_reason"] is None for c in candidates)
    assert all(c["candidate_id"] for c in candidates)
    assert all(c["created_at"] for c in candidates)


def test_generate_candidates_filters_below_min_score():
    signals = [
        {"signal_id": "S1", "entity_id": "AAPL", "score": 0.10},
        {"signal_id": "S2", "entity_id": "MSFT", "score": 0.01},
    ]
    # S2 (0.01) is below min_score=0.05, so only S1 passes
    candidates = generate_candidates(signals, min_score=0.05)
    assert len(candidates) == 1
    assert candidates[0]["signal_id"] == "S1"

    # Both pass when min_score is low enough
    candidates = generate_candidates(signals, min_score=0.01)
    assert len(candidates) == 2

    # Only S1 passes at 0.10 threshold
    candidates = generate_candidates(signals, min_score=0.10)
    assert len(candidates) == 1
    assert candidates[0]["signal_id"] == "S1"


def test_generate_candidates_top_n():
    signals = [{"signal_id": f"S{i}", "entity_id": "X", "score": i * 0.01} for i in range(1, 20)]
    candidates = generate_candidates(signals, top_n=5, min_score=0.01)
    assert len(candidates) == 5
    # Sorted descending by score
    scores = [c["score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_generate_candidates_sorted_descending():
    signals = [
        {"signal_id": "S1", "entity_id": "A", "score": 0.05},
        {"signal_id": "S2", "entity_id": "B", "score": 0.30},
        {"signal_id": "S3", "entity_id": "C", "score": 0.15},
    ]
    candidates = generate_candidates(signals)
    scores = [c["score"] for c in candidates]
    assert scores == [0.30, 0.15, 0.05]


def test_generate_candidates_empty_input():
    candidates = generate_candidates([])
    assert candidates == []


def test_generate_candidates_all_below_threshold():
    signals = [
        {"signal_id": "S1", "entity_id": "A", "score": 0.01},
        {"signal_id": "S2", "entity_id": "B", "score": 0.02},
    ]
    candidates = generate_candidates(signals, min_score=0.05)
    assert candidates == []


def test_direction_score_passthrough():
    """direction_score from signal should be passed through to candidate."""
    signals = [
        {"signal_id": "S1", "entity_id": "AAPL", "score": 0.20, "direction_score": 0.45},
    ]
    candidates = generate_candidates(signals)
    assert len(candidates) == 1
    assert candidates[0]["direction_score"] == 0.45


def test_direction_score_default():
    """Missing direction_score in signal should default to 0.0 in candidate."""
    signals = [
        {"signal_id": "S1", "entity_id": "AAPL", "score": 0.20},
    ]
    candidates = generate_candidates(signals)
    assert len(candidates) == 1
    assert candidates[0]["direction_score"] == 0.0
