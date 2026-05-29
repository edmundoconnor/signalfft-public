"""
Alpaca paper trading broker adapter.

Submits real market orders to Alpaca's paper trading sandbox.
Orders execute against live market data with simulated fills.

Position sizing: fixed $2,000 notional per trade.
Order type: market orders only.

Env vars:
  ALPACA_API_KEY: Alpaca API key ID
  ALPACA_SECRET_KEY: Alpaca API secret key
  ALPACA_NOTIONAL: dollars per trade (default 2000)
"""

import logging
import os
import re
import time
from datetime import datetime, timezone

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from execution.adapters.base import BrokerAdapter

logger = logging.getLogger(__name__)

# Symbols that are not tradeable tickers
_NON_TRADEABLE = {"MARKET_GENERAL", "SOCIAL_GENERAL"}

# CIK numbers — all digits, or prefixed with "CIK_" (SEC entity IDs, not ticker symbols)
_CIK_PATTERN = re.compile(r"^(CIK_)?\d+$")


class AlpacaBroker(BrokerAdapter):
    """
    BrokerAdapter implementation using Alpaca's paper trading API.

    Submits notional market orders ($2,000 per trade by default).
    Alpaca handles fractional shares automatically with notional orders.
    Polls for fill up to 30 seconds for market orders.
    Returns fill details compatible with TelemetryRecorder.
    """

    def __init__(self):
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.notional = float(os.environ.get("ALPACA_NOTIONAL", "2000"))

        if not api_key or not secret_key:
            logger.error("Alpaca API keys not configured — adapter disabled")
            self.client = None
            self.enabled = False
            return

        self.client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,
        )
        self.enabled = True
        logger.info("Alpaca paper trading adapter initialized (notional=$%s)", self.notional)

    @staticmethod
    def _extract_symbol(entity_id: str) -> str | None:
        """
        Extract a tradeable ticker symbol from entity_id.

        Returns None if the entity_id is not a tradeable symbol:
        - Strips "ENTITY#" prefix if present
        - Rejects MARKET_GENERAL, SOCIAL_GENERAL
        - Rejects CIK numbers (all digits)
        """
        symbol = entity_id.removeprefix("ENTITY#")
        if symbol in _NON_TRADEABLE:
            return None
        if _CIK_PATTERN.match(symbol):
            return None
        return symbol

    def submit_order(self, order: dict) -> dict:
        """
        Submit a market order to Alpaca.

        Returns a dict compatible with PaperTradeBroker output so that
        TelemetryRecorder and the router can process it identically.
        """
        candidate_id = order.get("candidate_id", "")
        entity_id = order.get("entity_id", "")
        submitted_at = datetime.now(timezone.utc)

        symbol = self._extract_symbol(entity_id)
        if symbol is None:
            return {
                "order_id": "",
                "candidate_id": candidate_id,
                "entity_id": entity_id,
                "direction": "BUY",
                "quantity": 0,
                "fill_price": 0.0,
                "slippage": 0.0,
                "latency_ms": 0,
                "status": "SKIPPED",
                "timestamp": submitted_at.isoformat(),
                "error": f"Non-tradeable symbol: {entity_id}",
            }

        if not self.enabled:
            return {
                "order_id": "",
                "candidate_id": candidate_id,
                "entity_id": entity_id,
                "direction": "BUY",
                "quantity": 0,
                "fill_price": 0.0,
                "slippage": 0.0,
                "latency_ms": 0,
                "status": "ERROR",
                "timestamp": submitted_at.isoformat(),
                "error": "Alpaca adapter disabled — API keys not configured",
            }

        try:
            order_request = MarketOrderRequest(
                symbol=symbol,
                notional=self.notional,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            alpaca_order = self.client.submit_order(order_request)
            logger.info(
                "Alpaca order submitted: id=%s symbol=%s notional=$%s",
                alpaca_order.id,
                symbol,
                self.notional,
            )

            return self._wait_for_fill(
                order_id=str(alpaca_order.id),
                candidate_id=candidate_id,
                entity_id=entity_id,
                submitted_at=submitted_at,
            )

        except APIError as e:
            logger.error("Alpaca API error for %s: %s", symbol, e)
            return {
                "order_id": "",
                "candidate_id": candidate_id,
                "entity_id": entity_id,
                "direction": "BUY",
                "quantity": 0,
                "fill_price": 0.0,
                "slippage": 0.0,
                "latency_ms": 0,
                "status": "ERROR",
                "timestamp": submitted_at.isoformat(),
                "error": str(e),
            }
        except Exception as e:
            logger.exception("Unexpected error submitting order for %s", symbol)
            return {
                "order_id": "",
                "candidate_id": candidate_id,
                "entity_id": entity_id,
                "direction": "BUY",
                "quantity": 0,
                "fill_price": 0.0,
                "slippage": 0.0,
                "latency_ms": 0,
                "status": "ERROR",
                "timestamp": submitted_at.isoformat(),
                "error": str(e),
            }

    def _wait_for_fill(
        self,
        order_id: str,
        candidate_id: str,
        entity_id: str,
        submitted_at: datetime,
        timeout_seconds: int = 30,
        poll_interval: float = 1.0,
    ) -> dict:
        """Poll Alpaca for order fill status up to timeout_seconds."""
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            alpaca_order = self.client.get_order_by_id(order_id)
            status = alpaca_order.status

            if status == OrderStatus.FILLED:
                filled_at = datetime.now(timezone.utc)
                fill_price = float(alpaca_order.filled_avg_price)
                filled_qty = float(alpaca_order.filled_qty)
                latency_ms = int((filled_at - submitted_at).total_seconds() * 1000)
                logger.info(
                    "Alpaca order filled: id=%s qty=%s @ $%s (latency=%dms)",
                    order_id,
                    filled_qty,
                    fill_price,
                    latency_ms,
                )
                return {
                    "order_id": order_id,
                    "candidate_id": candidate_id,
                    "entity_id": entity_id,
                    "direction": "BUY",
                    "quantity": filled_qty,
                    "fill_price": fill_price,
                    "slippage": 0.0,
                    "latency_ms": latency_ms,
                    "status": "FILLED",
                    "timestamp": filled_at.isoformat(),
                }

            if status in (OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
                logger.warning("Alpaca order %s ended with status %s", order_id, status.value)
                return {
                    "order_id": order_id,
                    "candidate_id": candidate_id,
                    "entity_id": entity_id,
                    "direction": "BUY",
                    "quantity": 0,
                    "fill_price": 0.0,
                    "slippage": 0.0,
                    "latency_ms": 0,
                    "status": status.value.upper(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            time.sleep(poll_interval)

        # Timeout — order still pending
        logger.warning("Alpaca order %s timed out after %ds (still pending)", order_id, timeout_seconds)
        return {
            "order_id": order_id,
            "candidate_id": candidate_id,
            "entity_id": entity_id,
            "direction": "BUY",
            "quantity": 0,
            "fill_price": 0.0,
            "slippage": 0.0,
            "latency_ms": 0,
            "status": "PENDING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_order_status(self, order_id: str) -> dict:
        """Check current status of an existing order."""
        if not self.enabled:
            return {"order_id": order_id, "status": "ERROR", "error": "Adapter disabled"}

        alpaca_order = self.client.get_order_by_id(order_id)
        result = {
            "order_id": order_id,
            "status": alpaca_order.status.value.upper(),
            "fill_price": float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None,
            "filled_qty": float(alpaca_order.filled_qty) if alpaca_order.filled_qty else None,
            "filled_at": alpaca_order.filled_at.isoformat() if alpaca_order.filled_at else None,
        }
        return result

    def get_account(self) -> dict:
        """Get current paper trading account info."""
        if not self.enabled:
            return {"error": "Adapter disabled"}

        account = self.client.get_account()
        return {
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "cash": float(account.cash),
            "positions_count": len(self.client.get_all_positions()),
        }
