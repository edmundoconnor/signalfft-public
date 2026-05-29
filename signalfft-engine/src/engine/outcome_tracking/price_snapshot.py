"""Price snapshot capture for outcome tracking.

Fetches price and liquidity data from Alpaca at the moment a signal fires,
then writes an outcome record to DynamoDB for later P&L measurement.

DynamoDB table definition (Terraform):
  -----------------------------------------------------------------------
  resource "aws_dynamodb_table" "outcomes" {
    name         = "${var.environment}-signalfft-outcomes"
    billing_mode = "PAY_PER_REQUEST"

    hash_key  = "PK"
    range_key = "SK"

    attribute {
      name = "PK"
      type = "S"
    }

    attribute {
      name = "SK"
      type = "S"
    }

    tags = {
      Project     = "signalfft"
      Environment = var.environment
    }
  }
  -----------------------------------------------------------------------

  Equivalent AWS CLI:
    aws dynamodb create-table \
      --table-name prod-signalfft-outcomes \
      --key-schema \
        AttributeName=PK,KeyType=HASH \
        AttributeName=SK,KeyType=RANGE \
      --attribute-definitions \
        AttributeName=PK,AttributeType=S \
        AttributeName=SK,AttributeType=S \
      --billing-mode PAY_PER_REQUEST

  SQS queue (add to signalfft-infra/modules/sqs/main.tf local.queues):
    "outcome-tracking" = {}

  Terraform env var (add to intelligence-pipeline service_env):
    { name = "OUTCOME_TRACKING_QUEUE_URL", value = var.queue_urls["outcome-tracking"] }

  The SignalScoringService fans out SignalScored events to this dedicated
  queue so OutcomeTrackingService does not compete with WaveEngine for
  messages on the shared signals queue.
"""

from __future__ import annotations

import logging
import re
import time
from decimal import Decimal
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame

from signalfft_common.dynamo.keys import build_outcomes_pk, build_outcomes_sk

logger = logging.getLogger(__name__)

# Retry configuration for Alpaca API calls
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds

# Entities containing these characters are clearly not stock tickers
_NON_TICKER_RE = re.compile(r"[_\s]")


def _is_non_ticker(entity_id: str) -> bool:
    """Return True if entity_id is clearly not a stock ticker symbol."""
    return bool(_NON_TICKER_RE.search(entity_id))


def _is_non_retryable(exc: Exception) -> bool:
    """Return True if the error indicates retrying won't help (403, 400)."""
    msg = str(exc).lower()
    return (
        "403" in msg
        or "forbidden" in msg
        or "invalid symbol" in msg
        or "subscription does not permit" in msg
    )


def _retry_alpaca(func, *args, **kwargs) -> Any:
    """Call *func* with retry + exponential backoff on transient Alpaca errors.

    Non-retryable errors (403, invalid symbol) are raised immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except (APIError, Exception) as exc:
            last_exc = exc
            if _is_non_retryable(exc):
                raise
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Alpaca API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _to_decimal(value: float | int | None) -> Decimal | None:
    """Convert a numeric value to Decimal for DynamoDB, or None."""
    if value is None:
        return None
    return Decimal(str(value))


def _fetch_latest_price(
    data_client: StockHistoricalDataClient,
    ticker: str,
) -> float | None:
    """Fetch the latest trade price for *ticker*. Returns None on failure."""
    try:
        request = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trades = _retry_alpaca(data_client.get_stock_latest_trade, request)
        trade = trades.get(ticker) if isinstance(trades, dict) else trades
        if trade is None:
            return None
        return float(trade.price)
    except Exception:
        logger.warning("Failed to fetch latest trade for %s", ticker, exc_info=True)
        return None


def _fetch_latest_quote(
    data_client: StockHistoricalDataClient,
    ticker: str,
) -> tuple[float | None, float | None]:
    """Fetch latest bid/ask for *ticker*. Returns (bid, ask) or (None, None)."""
    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = _retry_alpaca(data_client.get_stock_latest_quote, request)
        quote = quotes.get(ticker) if isinstance(quotes, dict) else quotes
        if quote is None:
            return None, None
        return float(quote.bid_price), float(quote.ask_price)
    except Exception:
        logger.warning("Failed to fetch latest quote for %s", ticker, exc_info=True)
        return None, None


def _compute_addv_20d(
    data_client: StockHistoricalDataClient,
    ticker: str,
) -> float | None:
    """Compute trailing 20-day average daily dollar volume.

    Fetches 20 trading days of daily bars, computes mean(close * volume).
    Returns None if insufficient data.
    """
    try:
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc)
        # Request ~30 calendar days to ensure we get 20 trading days
        start = end - timedelta(days=35)

        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            limit=20,
            feed=DataFeed.IEX,
        )
        bars_response = _retry_alpaca(data_client.get_stock_bars, request)
        bars = bars_response.get(ticker) if isinstance(bars_response, dict) else bars_response

        if not bars or len(bars) == 0:
            logger.warning("No bar data for %s, ADDV will be null", ticker)
            return None

        dollar_volumes = [float(bar.close) * float(bar.volume) for bar in bars]
        return sum(dollar_volumes) / len(dollar_volumes)

    except Exception:
        logger.warning("ADDV calculation failed for %s", ticker, exc_info=True)
        return None


def capture_price_snapshot(
    data_client: StockHistoricalDataClient,
    outcomes_table: Any,
    signal_id: str,
    entity_id: str,
    signal_timestamp: str,
    signal_score: float,
    direction_score: float = 0.0,
) -> dict:
    """Capture price/liquidity data for a signal and write an outcome record.

    Parameters
    ----------
    data_client:
        alpaca-py ``StockHistoricalDataClient`` for market data.
    outcomes_table:
        boto3 DynamoDB Table resource for the outcomes table.
    signal_id:
        UUID of the signal that fired.
    entity_id:
        Ticker symbol (e.g. ``"BSX"``).
    signal_timestamp:
        ISO 8601 timestamp of the signal.
    signal_score:
        Composite signal score (0.0–1.0).

    Returns
    -------
    dict
        The outcome record that was written to DynamoDB.
    """
    ticker = entity_id

    # Skip entities that are clearly not stock tickers (e.g. MARKET_GENERAL)
    if _is_non_ticker(entity_id):
        logger.info(
            "Skipping non-ticker entity %s — writing PRICE_UNAVAILABLE", entity_id
        )
        item = {
            "PK": build_outcomes_pk(entity_id),
            "SK": build_outcomes_sk(signal_id),
            "signal_id": signal_id,
            "entity_id": entity_id,
            "ticker": ticker,
            "signal_timestamp": signal_timestamp,
            "price_at_signal": None,
            "bid_at_signal": None,
            "ask_at_signal": None,
            "spread_at_signal": None,
            "addv_20d": None,
            "signal_score": _to_decimal(signal_score),
            "direction_score": _to_decimal(direction_score),
            "status": "PRICE_UNAVAILABLE",
            "price_t1h": None,
            "price_t4h": None,
            "price_t1d": None,
            "price_t5d": None,
            "raw_pct_change_t1d": None,
            "spread_adj_pct_change_t1d": None,
            "raw_pct_change_t5d": None,
            "spread_adj_pct_change_t5d": None,
        }
        outcomes_table.put_item(Item=item)
        logger.info(
            "Outcome recorded: entity=%s signal=%s status=PRICE_UNAVAILABLE price=None",
            entity_id, signal_id,
        )
        return item

    # Attempt to fetch market data
    price = _fetch_latest_price(data_client, ticker)
    bid, ask = _fetch_latest_quote(data_client, ticker)
    addv = _compute_addv_20d(data_client, ticker)

    # Determine status
    if price is None and bid is None:
        status = "PRICE_UNAVAILABLE"
        logger.warning(
            "Ticker %s not available on Alpaca — writing outcome with PRICE_UNAVAILABLE",
            ticker,
        )
    else:
        status = "CAPTURED"

    spread = None
    if bid is not None and ask is not None:
        spread = ask - bid

    item = {
        "PK": build_outcomes_pk(entity_id),
        "SK": build_outcomes_sk(signal_id),
        "signal_id": signal_id,
        "entity_id": entity_id,
        "ticker": ticker,
        "signal_timestamp": signal_timestamp,
        "price_at_signal": _to_decimal(price),
        "bid_at_signal": _to_decimal(bid),
        "ask_at_signal": _to_decimal(ask),
        "spread_at_signal": _to_decimal(spread),
        "addv_20d": _to_decimal(addv),
        "signal_score": _to_decimal(signal_score),
        "direction_score": _to_decimal(direction_score),
        "status": status,
        # Deferred fields — populated later by the outcome collector
        "price_t1h": None,
        "price_t4h": None,
        "price_t1d": None,
        "price_t5d": None,
        "raw_pct_change_t1d": None,
        "spread_adj_pct_change_t1d": None,
        "raw_pct_change_t5d": None,
        "spread_adj_pct_change_t5d": None,
    }

    outcomes_table.put_item(Item=item)
    logger.info(
        "Outcome recorded: entity=%s signal=%s status=%s price=%s",
        entity_id, signal_id, status, price,
    )
    return item
