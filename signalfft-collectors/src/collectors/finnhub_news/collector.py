"""Finnhub financial news collector.

Fetches general market news from Finnhub's free API and normalizes to
the BaseCollector document format for dedup, S3 storage, and SQS emission.

Free tier: 60 calls/minute. We use 1 call per Lambda invocation.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from collectors.base import BaseCollector, make_lambda_handler

logger = logging.getLogger(__name__)

FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"


class FinnhubNewsCollector(BaseCollector):
    """Collects general financial news from Finnhub API."""

    source_name = "FINNHUB_NEWS"

    def __init__(self):
        super().__init__()
        self._api_key = os.environ.get("FINNHUB_API_KEY", "")
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def collect(self) -> list[dict[str, Any]]:
        """Fetch general market news from Finnhub.

        GET /news?category=general&token=API_KEY
        Returns list of news item dicts, each containing:
        id, headline, summary, source, url, datetime, related, image
        """
        if not self._api_key:
            logger.error("FINNHUB_API_KEY not set — skipping collection")
            return []

        try:
            response = self._session.get(
                FINNHUB_NEWS_URL,
                params={"category": "general", "token": self._api_key},
                timeout=30,
            )
            if response.status_code == 429:
                logger.warning("Finnhub rate limit hit (429) — will retry next invocation")
                return []
            response.raise_for_status()
            items = response.json()
            if not isinstance(items, list):
                logger.warning("Finnhub returned non-list response: %s", type(items))
                return []
            logger.info("Finnhub: fetched %d news items", len(items))
            return items
        except requests.RequestException:
            logger.exception("Failed to fetch Finnhub news")
            return []

    def extract_entity_id(self, doc: dict[str, Any]) -> str:
        """Extract ticker from the 'related' field.

        Finnhub returns 'related' as a comma-separated string like "AAPL,MSFT".
        Use the first ticker if available, otherwise "MARKET_GENERAL".
        """
        related = doc.get("related", "")
        if related:
            tickers = [t.strip() for t in related.split(",") if t.strip()]
            if tickers:
                return tickers[0]
        return "MARKET_GENERAL"

    def extract_event_type(self, doc: dict[str, Any]) -> str:
        """All Finnhub news items are NEWS_ARTICLE events."""
        return "NEWS_ARTICLE"


lambda_handler = make_lambda_handler(FinnhubNewsCollector)
