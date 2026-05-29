"""Reddit financial subreddit collector.

Fetches recent posts from financial subreddits (r/stocks, r/investing,
r/wallstreetbets, r/StockMarket) and normalizes them to the BaseCollector
document format.

Uses Reddit OAuth API (required for cloud/server access). Requires a Reddit
"script" app registered at https://www.reddit.com/prefs/apps. Credentials
are loaded from environment variables (typically sourced from SSM).

If OAuth credentials are not configured, the collector logs a warning and
returns an empty list (no crash).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from collectors.base import BaseCollector, make_lambda_handler

logger = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = ["stocks", "investing", "wallstreetbets", "StockMarket"]
REDDIT_USER_AGENT = "linux:signalfft:v1.0 (by /u/signalfft_bot)"

# Top ~100 tickers by market cap for $TICKER extraction. Kept minimal to avoid
# false positives. The downstream feature extractor does thorough entity resolution.
TOP_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK",
    "UNH", "LLY", "JPM", "XOM", "V", "JNJ", "AVGO", "PG", "MA", "HD",
    "COST", "MRK", "ABBV", "AMD", "CRM", "NFLX", "CVX", "KO", "PEP",
    "ADBE", "TMO", "BAC", "WMT", "MCD", "CSCO", "ABT", "ACN", "DHR",
    "ORCL", "LIN", "NKE", "TXN", "INTC", "QCOM", "UNP", "CMCSA",
    "INTU", "AMGN", "PM", "HON", "LOW", "IBM", "GE", "AMAT", "CAT",
    "BA", "GS", "SBUX", "PFE", "BLK", "RTX", "ISRG", "MS", "BKNG",
    "T", "MDLZ", "SPGI", "SYK", "ADP", "NOW", "GILD", "VRTX", "MMC",
    "PLTR", "SOFI", "RIVN", "LCID", "NIO", "PYPL", "SQ", "COIN", "HOOD",
    "GME", "AMC", "BBBY", "WISH", "CLOV", "BB", "NOK", "MARA", "RIOT",
    "DKNG", "ABNB", "RBLX", "SNAP", "PINS", "UBER", "LYFT", "DIS", "F",
    "GM", "SHOP", "SE", "ROKU", "ZM", "CRWD", "NET", "SNOW", "PANW",
}

# Pattern to match $TICKER (most reliable signal from Reddit posts)
DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"


class RedditCollector(BaseCollector):
    """Collects recent posts from financial subreddits."""

    source_name = "REDDIT"

    def __init__(self):
        super().__init__()
        subreddits_env = os.environ.get("REDDIT_SUBREDDITS", "")
        self._subreddits = (
            [s.strip() for s in subreddits_env.split(",") if s.strip()]
            if subreddits_env
            else DEFAULT_SUBREDDITS
        )
        self._min_score = int(os.environ.get("REDDIT_MIN_SCORE", "5"))
        self._max_age_hours = int(os.environ.get("REDDIT_MAX_AGE_HOURS", "2"))
        self._client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        self._client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": REDDIT_USER_AGENT,
        })
        self._access_token: str | None = None

    def _authenticate(self) -> bool:
        """Obtain an OAuth2 access token using client credentials grant.

        Reddit "script" apps use application-only OAuth:
        POST /api/v1/access_token with grant_type=client_credentials
        """
        if not self._client_id or not self._client_secret:
            logger.error(
                "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET not set — "
                "skipping Reddit collection. Register a script app at "
                "https://www.reddit.com/prefs/apps"
            )
            return False

        try:
            response = requests.post(
                REDDIT_TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": REDDIT_USER_AGENT},
                timeout=15,
            )
            response.raise_for_status()
            token_data = response.json()
            self._access_token = token_data.get("access_token")
            if not self._access_token:
                logger.error("Reddit OAuth response missing access_token")
                return False
            self._session.headers["Authorization"] = f"Bearer {self._access_token}"
            logger.info("Reddit OAuth token obtained")
            return True
        except requests.RequestException:
            logger.exception("Failed to obtain Reddit OAuth token")
            return False

    def collect(self) -> list[dict[str, Any]]:
        """Fetch recent posts from all configured subreddits.

        Adds 1-second delay between subreddit fetches to respect rate limits.
        """
        if not self._authenticate():
            return []

        all_posts: list[dict[str, Any]] = []
        for i, subreddit in enumerate(self._subreddits):
            if i > 0:
                time.sleep(1)
            try:
                posts = self._fetch_subreddit(subreddit)
                all_posts.extend(posts)
            except Exception:
                logger.exception("Failed to fetch r/%s", subreddit)

        logger.info(
            "Reddit: fetched %d posts from %s",
            len(all_posts),
            ", ".join(self._subreddits),
        )
        return all_posts

    def _fetch_subreddit(self, subreddit: str, limit: int = 25) -> list[dict[str, Any]]:
        """Fetch recent posts from a single subreddit via OAuth API."""
        url = f"{REDDIT_OAUTH_BASE}/r/{subreddit}/new"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._max_age_hours)

        try:
            response = self._session.get(
                url, params={"limit": limit}, timeout=15,
            )
        except requests.RequestException:
            logger.exception("Request failed for r/%s", subreddit)
            return []

        if response.status_code == 429:
            logger.warning("Reddit rate limited on r/%s — sleeping 2s and retrying", subreddit)
            time.sleep(2)
            try:
                response = self._session.get(
                    url, params={"limit": limit}, timeout=15,
                )
            except requests.RequestException:
                logger.exception("Retry failed for r/%s", subreddit)
                return []
            if response.status_code != 200:
                logger.warning("Retry for r/%s returned %d — skipping", subreddit, response.status_code)
                return []

        if response.status_code in (403, 404):
            logger.warning("r/%s returned %d — skipping", subreddit, response.status_code)
            return []

        if response.status_code != 200:
            logger.warning("r/%s returned %d", subreddit, response.status_code)
            return []

        try:
            data = response.json()
        except ValueError:
            logger.warning("r/%s returned invalid JSON", subreddit)
            return []

        posts = []
        for child in data.get("data", {}).get("children", []):
            post_data = child.get("data", {})
            created_utc = post_data.get("created_utc", 0)
            post_time = datetime.fromtimestamp(created_utc, tz=timezone.utc)

            # Filter: too old
            if post_time < cutoff:
                continue

            # Filter: low score
            score = post_data.get("score", 0)
            if score < self._min_score:
                continue

            # Build the document dict that BaseCollector will process
            post = {
                "reddit_id": post_data.get("id", ""),
                "title": post_data.get("title", ""),
                "selftext": post_data.get("selftext", ""),
                "subreddit": subreddit,
                "author": post_data.get("author", ""),
                "score": score,
                "num_comments": post_data.get("num_comments", 0),
                "url": f"https://www.reddit.com{post_data.get('permalink', '')}",
                "published_at": post_time.isoformat(),
            }
            posts.append(post)

        return posts

    def extract_entity_id(self, doc: dict[str, Any]) -> str:
        """Extract a stock ticker from the post title and body.

        Strategy (conservative to avoid false positives):
        1. Look for $TICKER pattern (e.g., "$AAPL") — most reliable on Reddit
        2. Fallback: check title words against top-100 tickers
        3. If nothing found, return "SOCIAL_GENERAL"
        """
        title = doc.get("title", "")
        selftext = doc.get("selftext", "")
        text = f"{title} {selftext}"

        # Strategy 1: $TICKER pattern
        matches = DOLLAR_TICKER_RE.findall(text)
        for ticker in matches:
            if ticker in TOP_TICKERS:
                return ticker

        # Strategy 2: bare all-caps word in title matching a known ticker
        for word in title.split():
            clean = word.strip(".,!?;:()[]{}\"'")
            if clean in TOP_TICKERS and len(clean) >= 2:
                return clean

        return "SOCIAL_GENERAL"

    def extract_event_type(self, doc: dict[str, Any]) -> str:
        """All Reddit posts are SOCIAL_POST events."""
        return "SOCIAL_POST"


lambda_handler = make_lambda_handler(RedditCollector)
