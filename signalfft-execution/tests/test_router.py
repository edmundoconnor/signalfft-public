"""Tests for the execution router."""

import os
import pytest
from unittest.mock import patch, MagicMock, call

from execution.router import ExecutionRouter


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set minimal env for router construction."""
    monkeypatch.setenv("INPUT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789/test-queue")
    monkeypatch.setenv("BROKER_MODE", "paper")
    monkeypatch.setenv("EXECUTION_TELEMETRY_TABLE", "")
    monkeypatch.delenv("GRAPH_EDGES_TABLE", raising=False)


def _make_router(**patches):
    with patch("execution.router.boto3") as mock_boto3:
        mock_boto3.client.return_value = MagicMock()
        with patch("execution.telemetry.boto3"):
            with patch("execution.router.GraphWriter", **patches) if patches else patch("execution.router.GraphWriter"):
                router = ExecutionRouter()
    return router


@pytest.fixture
def router():
    return _make_router()


def test_process_candidate(router):
    """Process a candidate message and verify outcome is returned."""
    message = {
        "event_type": "TRADE_CANDIDATE_GENERATED",
        "payload": {
            "candidate_id": "tc-001",
            "signal_id": "sig-001",
            "entity_id": "ent-001",
            "score": 0.87,
            "risk_status": "APPROVED",
        },
    }

    outcome = router.process_candidate(message)

    assert outcome is not None
    assert outcome["candidate_id"] == "tc-001"
    assert outcome["entity_id"] == "ent-001"
    assert "fill_price" in outcome
    assert outcome["status"] == "FILLED"


def test_paper_mode_default(monkeypatch):
    """Without BROKER_MODE set, default to paper and no error."""
    monkeypatch.delenv("BROKER_MODE", raising=False)
    with patch("execution.router.boto3") as mock_boto3:
        mock_boto3.client.return_value = MagicMock()
        with patch("execution.telemetry.boto3"):
            with patch("execution.router.GraphWriter"):
                router = ExecutionRouter()
    from execution.adapters.paper_trade import PaperTradeBroker
    assert isinstance(router._broker, PaperTradeBroker)


def test_invalid_broker_mode(monkeypatch):
    """Setting BROKER_MODE to unsupported value raises ValueError."""
    monkeypatch.setenv("BROKER_MODE", "live")
    with patch("execution.router.boto3") as mock_boto3:
        mock_boto3.client.return_value = MagicMock()
        with patch("execution.telemetry.boto3"):
            with patch("execution.router.GraphWriter"):
                with pytest.raises(ValueError, match="Unsupported broker mode"):
                    ExecutionRouter()


def test_graph_feedback_called(monkeypatch):
    """When GRAPH_EDGES_TABLE is set, link_signal_outcome is called after fill."""
    monkeypatch.setenv("GRAPH_EDGES_TABLE", "test-graph-edges")

    mock_gw_instance = MagicMock()
    mock_gw_cls = MagicMock(return_value=mock_gw_instance)

    with patch("execution.router.boto3") as mock_boto3:
        mock_boto3.client.return_value = MagicMock()
        with patch("execution.telemetry.boto3"):
            with patch("execution.router.GraphWriter", mock_gw_cls):
                router = ExecutionRouter()

    message = {
        "event_type": "TRADE_CANDIDATE_GENERATED",
        "payload": {
            "candidate_id": "tc-001",
            "signal_id": "sig-001",
            "entity_id": "ent-001",
            "score": 0.87,
            "risk_status": "APPROVED",
        },
    }

    outcome = router.process_candidate(message)

    mock_gw_instance.link_signal_outcome.assert_called_once()
    call_kwargs = mock_gw_instance.link_signal_outcome.call_args
    assert call_kwargs[1]["signal_id"] == "sig-001" or call_kwargs[0][0] == "sig-001"
    assert outcome is not None


def test_no_graph_feedback_without_table(router):
    """When GRAPH_EDGES_TABLE is not set, graph_writer is None."""
    assert router._graph_writer is None


# ===========================================================================
# Direction routing tests
# ===========================================================================


def test_short_candidate_returns_none(router):
    """SHORT direction should be skipped — returns None."""
    message = {
        "payload": {
            "candidate_id": "tc-short",
            "signal_id": "sig-short",
            "entity_id": "AAPL",
            "score": 0.87,
            "risk_status": "APPROVED",
            "direction": "SHORT",
        },
    }
    outcome = router.process_candidate(message)
    assert outcome is None


def test_neutral_candidate_returns_none(router):
    """NEUTRAL direction should be skipped — returns None."""
    message = {
        "payload": {
            "candidate_id": "tc-neutral",
            "signal_id": "sig-neutral",
            "entity_id": "AAPL",
            "score": 0.87,
            "risk_status": "APPROVED",
            "direction": "NEUTRAL",
        },
    }
    outcome = router.process_candidate(message)
    assert outcome is None


def test_long_candidate_executed(router):
    """LONG direction should be executed normally."""
    message = {
        "payload": {
            "candidate_id": "tc-long",
            "signal_id": "sig-long",
            "entity_id": "AAPL",
            "score": 0.87,
            "risk_status": "APPROVED",
            "direction": "LONG",
        },
    }
    outcome = router.process_candidate(message)
    assert outcome is not None
    assert outcome["status"] == "FILLED"


def test_missing_direction_defaults_to_buy(router):
    """Missing direction field should default to BUY flow (backwards compat)."""
    message = {
        "payload": {
            "candidate_id": "tc-nodir",
            "signal_id": "sig-nodir",
            "entity_id": "AAPL",
            "score": 0.87,
            "risk_status": "APPROVED",
        },
    }
    outcome = router.process_candidate(message)
    assert outcome is not None
    assert outcome["status"] == "FILLED"


def test_empty_direction_defaults_to_buy(router):
    """Empty string direction should default to BUY flow (backwards compat)."""
    message = {
        "payload": {
            "candidate_id": "tc-empty",
            "signal_id": "sig-empty",
            "entity_id": "AAPL",
            "score": 0.87,
            "risk_status": "APPROVED",
            "direction": "",
        },
    }
    outcome = router.process_candidate(message)
    assert outcome is not None
    assert outcome["status"] == "FILLED"
