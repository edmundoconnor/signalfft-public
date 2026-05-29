"""Abstract broker adapter interface. All broker integrations implement this."""

from abc import ABC, abstractmethod


class BrokerAdapter(ABC):
    @abstractmethod
    def submit_order(self, order: dict) -> dict:
        """
        Submit an order to the broker.
        Input order: {candidate_id, entity_id, direction, quantity, order_type, limit_price (optional)}
        Returns: {order_id, status, fill_price, slippage, latency_ms, timestamp}
        """

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Returns current status of an order by order_id."""
