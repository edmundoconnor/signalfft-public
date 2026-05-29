"""Deferred price collector for outcome tracking.

Scans the outcomes table for CAPTURED records with unfilled price windows,
fetches bar close prices from Alpaca once each window has elapsed, and
atomically updates the outcome record with prices and return calculations.

This is a standalone Lambda — it does NOT extend BaseCollector because the
workflow is scan-fetch-update rather than collect-dedup-store-emit.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from signalfft_common.config import get_secret_env

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / conversion helpers (mirror engine/outcome_tracking/price_snapshot.py)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds

# Market close approximation in UTC (4 PM ET ~ 20:00 UTC)
_MARKET_CLOSE_HOUR = 20


def _retry_alpaca(func, *args, **kwargs) -> Any:
    """Call *func* with retry + exponential backoff on Alpaca API errors."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except APIError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Alpaca API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Alpaca call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _to_decimal(value: float | int | None) -> Decimal | None:
    """Convert a numeric value to Decimal for DynamoDB, or None."""
    if value is None:
        return None
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Trading day helpers
# ---------------------------------------------------------------------------


def _nth_trading_day_close(dt: datetime, n: int) -> datetime:
    """Return the Nth trading-day close (20:00 UTC) after *dt*.

    Trading days are weekdays (Mon–Fri). No holiday calendar — weekday
    approximation per spec.
    """
    current = dt.date() + timedelta(days=1)
    count = 0
    while True:
        if current.weekday() < 5:  # Mon=0 … Fri=4
            count += 1
            if count == n:
                return datetime(
                    current.year, current.month, current.day,
                    _MARKET_CLOSE_HOUR, 0, 0,
                    tzinfo=timezone.utc,
                )
        current += timedelta(days=1)


def _next_trading_day(dt: datetime) -> datetime:
    """Return the next trading-day close (20:00 UTC) after *dt*."""
    return _nth_trading_day_close(dt, 1)


# ---------------------------------------------------------------------------
# Window elapsed logic
# ---------------------------------------------------------------------------

# Window definitions: name → function(signal_dt) → target datetime
_WINDOW_TARGET = {
    "t1h": lambda dt: dt + timedelta(hours=1),
    "t4h": lambda dt: dt + timedelta(hours=4),
    "t1d": lambda dt: _nth_trading_day_close(dt, 1),
    "t5d": lambda dt: _nth_trading_day_close(dt, 5),
}


def _window_elapsed(
    signal_ts: str, window: str, now: datetime,
) -> tuple[bool, datetime]:
    """Check whether *window* has elapsed since *signal_ts*.

    Returns (elapsed: bool, target_dt: datetime).
    """
    dt = datetime.fromisoformat(signal_ts)
    target = _WINDOW_TARGET[window](dt)
    return now >= target, target


# ---------------------------------------------------------------------------
# Price fetching
# ---------------------------------------------------------------------------


def _fetch_hourly_bar_close(
    data_client: StockHistoricalDataClient,
    ticker: str,
    target_dt: datetime,
) -> float | None:
    """Fetch the hourly bar close price nearest to *target_dt*."""
    try:
        start = target_dt - timedelta(hours=2)
        end = target_dt + timedelta(hours=1)
        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Hour,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars_response = _retry_alpaca(data_client.get_stock_bars, request)
        try:
            bars = bars_response[ticker]
        except (KeyError, TypeError):
            return None
        if not bars:
            return None
        best = min(bars, key=lambda b: abs((b.timestamp - target_dt).total_seconds()))
        return float(best.close)
    except Exception:
        logger.warning(
            "Failed to fetch hourly bar for %s at %s", ticker, target_dt,
            exc_info=True,
        )
        return None


def _fetch_daily_bar_close(
    data_client: StockHistoricalDataClient,
    ticker: str,
    target_dt: datetime,
) -> float | None:
    """Fetch the daily bar close price for the trading day at *target_dt*."""
    try:
        start = target_dt - timedelta(days=2)
        end = target_dt + timedelta(days=1)
        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars_response = _retry_alpaca(data_client.get_stock_bars, request)
        try:
            bars = bars_response[ticker]
        except (KeyError, TypeError):
            return None
        if not bars:
            return None
        target_date = target_dt.date()
        best = min(bars, key=lambda b: abs((b.timestamp.date() - target_date).days))
        return float(best.close)
    except Exception:
        logger.warning(
            "Failed to fetch daily bar for %s at %s", ticker, target_dt,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Return calculations
# ---------------------------------------------------------------------------


def _compute_returns(
    price_at_signal: float | None,
    spread_at_signal: float | None,
    deferred_price: float | None,
) -> dict[str, float | None]:
    """Compute raw and spread-adjusted percentage change.

    spread_adj subtracts the round-trip spread cost from the gain:
        (deferred - initial - spread) / initial * 100
    """
    if price_at_signal is None or price_at_signal == 0 or deferred_price is None:
        return {"raw_pct_change": None, "spread_adj_pct_change": None}

    raw_pct = (deferred_price - price_at_signal) / price_at_signal * 100

    if spread_at_signal is not None:
        spread_adj_pct = (
            (deferred_price - price_at_signal - spread_at_signal)
            / price_at_signal * 100
        )
    else:
        spread_adj_pct = None

    return {"raw_pct_change": raw_pct, "spread_adj_pct_change": spread_adj_pct}


# ---------------------------------------------------------------------------
# OutcomeCollector
# ---------------------------------------------------------------------------

# Windows that get pct_change fields (matches schema from price_snapshot.py)
_WINDOWS_WITH_RETURNS = {"t1d", "t5d"}
_ALL_WINDOWS = ["t1h", "t4h", "t1d", "t5d"]


class OutcomeCollector:
    """Scan outcomes with unfilled deferred prices and fill them in."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "prod")
        self._table_name = os.environ.get(
            "OUTCOMES_TABLE", f"{self._env}-signalfft-outcomes",
        )
        self._scan_limit = int(os.environ.get("OUTCOME_SCAN_LIMIT", "500"))

        dynamodb = boto3.resource("dynamodb", region_name=self._region)
        self._table = dynamodb.Table(self._table_name)

        api_key = get_secret_env("ALPACA_API_KEY")
        secret_key = get_secret_env("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            self._data_client = StockHistoricalDataClient(api_key, secret_key)
        else:
            self._data_client = None
            logger.warning(
                "Alpaca keys not configured — deferred price fetching disabled",
            )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan_eligible_outcomes(self) -> list[dict]:
        """Return CAPTURED outcomes that have at least one NULL price window.

        Paginates through DynamoDB scan results and filters out untradeable
        entities (MARKET_GENERAL, unresolved CIKs) at scan time.
        """
        filter_expr = (
            Attr("status").eq("CAPTURED")
            & (
                Attr("price_t1h").attribute_type("NULL")
                | Attr("price_t4h").attribute_type("NULL")
                | Attr("price_t1d").attribute_type("NULL")
                | Attr("price_t5d").attribute_type("NULL")
            )
            & ~Attr("PK").begins_with("ENTITY#MARKET_GENERAL")
            & ~Attr("PK").begins_with("ENTITY#CIK_")
        )

        items: list[dict] = []
        kwargs: dict[str, Any] = {"FilterExpression": filter_expr}

        while True:
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            if len(items) >= self._scan_limit:
                items = items[: self._scan_limit]
                break
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        return items

    # ------------------------------------------------------------------
    # Process a single outcome
    # ------------------------------------------------------------------

    def process_outcome(
        self, item: dict, now: datetime,
    ) -> dict[str, bool]:
        """Fill in elapsed deferred price windows for one outcome.

        Returns ``{"updated": bool, "completed": bool}``.
        """
        signal_ts = item["signal_timestamp"]
        ticker = item["ticker"]
        price_at_signal = (
            float(item["price_at_signal"])
            if item.get("price_at_signal") is not None
            else None
        )
        spread_at_signal = (
            float(item["spread_at_signal"])
            if item.get("spread_at_signal") is not None
            else None
        )

        updates: dict[str, Any] = {}
        filled_count = 0

        for window in _ALL_WINDOWS:
            price_field = f"price_{window}"

            # Already filled
            if item.get(price_field) is not None:
                filled_count += 1
                continue

            elapsed, target_dt = _window_elapsed(signal_ts, window, now)
            if not elapsed:
                continue

            # Fetch the bar close
            if window in ("t1h", "t4h"):
                price = _fetch_hourly_bar_close(
                    self._data_client, ticker, target_dt,
                )
            else:
                price = _fetch_daily_bar_close(
                    self._data_client, ticker, target_dt,
                )

            if price is None:
                continue

            updates[price_field] = _to_decimal(price)
            filled_count += 1

            # Compute returns for t1d / t5d only
            if window in _WINDOWS_WITH_RETURNS:
                returns = _compute_returns(
                    price_at_signal, spread_at_signal, price,
                )
                if returns["raw_pct_change"] is not None:
                    updates[f"raw_pct_change_{window}"] = _to_decimal(
                        returns["raw_pct_change"],
                    )
                if returns["spread_adj_pct_change"] is not None:
                    updates[f"spread_adj_pct_change_{window}"] = _to_decimal(
                        returns["spread_adj_pct_change"],
                    )

        # All 4 price windows filled → mark COMPLETE
        if filled_count == len(_ALL_WINDOWS):
            updates["status"] = "COMPLETE"

        if updates:
            self._update_outcome_fields(item["PK"], item["SK"], updates)
            return {
                "updated": True,
                "completed": updates.get("status") == "COMPLETE",
            }
        return {"updated": False, "completed": False}

    # ------------------------------------------------------------------
    # Atomic DynamoDB update
    # ------------------------------------------------------------------

    def _update_outcome_fields(
        self, pk: str, sk: str, updates: dict[str, Any],
    ) -> None:
        """Atomically SET the given fields on an outcome item."""
        expr_parts: list[str] = []
        attr_names: dict[str, str] = {}
        attr_values: dict[str, Any] = {}

        for i, (field, value) in enumerate(updates.items()):
            name_placeholder = f"#f{i}"
            value_placeholder = f":v{i}"
            expr_parts.append(f"{name_placeholder} = {value_placeholder}")
            attr_names[name_placeholder] = field
            attr_values[value_placeholder] = value

        self._table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> dict[str, int]:
        """Execute one pass: scan → fetch → update. Returns stats dict."""
        stats = {
            "scanned": 0,
            "updated": 0,
            "completed": 0,
            "skipped": 0,
            "errors": 0,
        }

        if self._data_client is None:
            logger.error("No Alpaca client — cannot fetch deferred prices")
            return stats

        now = datetime.now(timezone.utc)
        items = self.scan_eligible_outcomes()
        stats["scanned"] = len(items)

        for item in items:
            try:
                result = self.process_outcome(item, now)
                if result["updated"]:
                    stats["updated"] += 1
                    if result["completed"]:
                        stats["completed"] += 1
                else:
                    stats["skipped"] += 1
            except Exception:
                logger.error(
                    "Error processing outcome %s/%s",
                    item.get("PK"), item.get("SK"),
                    exc_info=True,
                )
                stats["errors"] += 1

        logger.info("Outcome collector run complete: %s", stats)
        return stats


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """AWS Lambda handler — instantiates OutcomeCollector and runs one pass."""
    collector = OutcomeCollector()
    stats = collector.run()
    return {"statusCode": 200, "body": json.dumps(stats)}
