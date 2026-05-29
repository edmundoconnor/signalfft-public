"""
Paper-trade mock broker. Simulates order execution with configurable latency and slippage.
No external dependencies — all fills are simulated locally.
"""

import random
import time
import uuid
from datetime import datetime, timezone

from execution.adapters.base import BrokerAdapter


class PaperTradeBroker(BrokerAdapter):
    def __init__(self, simulated_latency_ms: int = 50, slippage_bps: float = 5.0):
        """
        Mock broker for paper trading.
        - simulated_latency_ms: simulated execution latency in milliseconds
        - slippage_bps: basis points of random slippage applied to fill price
        - orders: dict storing all orders by order_id (in-memory)
        """
        self.simulated_latency_ms = simulated_latency_ms
        self.slippage_bps = slippage_bps
        self.orders: dict[str, dict] = {}

    def submit_order(self, order: dict) -> dict:
        """
        Simulate order execution:
        1. Generate order_id (UUID).
        2. Simulate fill price with slippage.
        3. Simulate latency.
        4. Store and return result.
        """
        order_id = str(uuid.uuid4())

        base_price = order.get("limit_price", 100.0)
        slippage_amount = base_price * (self.slippage_bps / 10000) * random.uniform(-1, 1)
        fill_price = round(base_price + slippage_amount, 4)

        if self.simulated_latency_ms > 0:
            time.sleep(self.simulated_latency_ms / 1000)

        result = {
            "order_id": order_id,
            "candidate_id": order["candidate_id"],
            "entity_id": order["entity_id"],
            "direction": order["direction"],
            "quantity": order["quantity"],
            "fill_price": fill_price,
            "slippage": round(slippage_amount, 4),
            "latency_ms": self.simulated_latency_ms,
            "status": "FILLED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.orders[order_id] = result
        return result

    def get_order_status(self, order_id: str) -> dict:
        """Return order from self.orders. Raise KeyError if not found."""
        return self.orders[order_id]
