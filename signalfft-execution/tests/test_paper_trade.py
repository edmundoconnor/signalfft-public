"""Tests for the paper-trade broker adapter."""

import uuid

from execution.adapters.paper_trade import PaperTradeBroker


def _make_order(**overrides):
    order = {
        "candidate_id": "tc-001",
        "entity_id": "ent-001",
        "direction": "BUY",
        "quantity": 100,
        "order_type": "MARKET",
        "limit_price": 50.0,
    }
    order.update(overrides)
    return order


def test_submit_order():
    broker = PaperTradeBroker(simulated_latency_ms=0)
    result = broker.submit_order(_make_order())

    assert result["status"] == "FILLED"
    assert abs(result["fill_price"] - 50.0) < 1.0  # within reasonable range
    assert "latency_ms" in result
    assert "slippage" in result
    assert result["candidate_id"] == "tc-001"
    assert result["entity_id"] == "ent-001"


def test_get_order_status():
    broker = PaperTradeBroker(simulated_latency_ms=0)
    result = broker.submit_order(_make_order())
    order_id = result["order_id"]

    status = broker.get_order_status(order_id)
    assert status["order_id"] == order_id
    assert status["status"] == "FILLED"
    assert status["fill_price"] == result["fill_price"]


def test_order_has_uuid():
    broker = PaperTradeBroker(simulated_latency_ms=0)
    result = broker.submit_order(_make_order())
    parsed = uuid.UUID(result["order_id"])
    assert str(parsed) == result["order_id"]


def test_slippage_within_bounds():
    broker = PaperTradeBroker(simulated_latency_ms=0, slippage_bps=10.0)
    base_price = 100.0
    max_slip = base_price * (10.0 / 10000)

    for _ in range(100):
        result = broker.submit_order(_make_order(limit_price=base_price))
        assert abs(result["slippage"]) <= max_slip + 1e-9


def test_zero_slippage():
    broker = PaperTradeBroker(simulated_latency_ms=0, slippage_bps=0.0)
    result = broker.submit_order(_make_order(limit_price=75.0))
    assert result["fill_price"] == 75.0
    assert result["slippage"] == 0.0


def test_default_price():
    broker = PaperTradeBroker(simulated_latency_ms=0, slippage_bps=0.0)
    order = _make_order()
    del order["limit_price"]
    result = broker.submit_order(order)
    assert result["fill_price"] == 100.0  # default mock price
