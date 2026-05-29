"""Bluesky financial social collector.

Searches the Bluesky API for posts containing stock cashtags
(e.g. $AAPL, $TSLA) and normalizes them to the BaseCollector document
format for dedup, S3 storage, and SQS emission.

Authenticates via com.atproto.server.createSession using an app password
stored in SSM, then searches via the authenticated PDS endpoint.

Bluesky search does not support boolean operators (OR/AND), so we issue
one query per search term and deduplicate by post URI.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from collectors.base import BaseCollector, make_lambda_handler

logger = logging.getLogger(__name__)

BLUESKY_PDS_BASE = "https://bsky.social/xrpc"
BLUESKY_SEARCH_URL = f"{BLUESKY_PDS_BASE}/app.bsky.feed.searchPosts"
BLUESKY_CREATE_SESSION_URL = f"{BLUESKY_PDS_BASE}/com.atproto.server.createSession"

DEFAULT_SEARCH_TERMS = "stocks,$AAPL,$TSLA,$NVDA,$MSFT,$AMZN"

# Match $TICKER cashtag pattern (1-5 uppercase letters after $)
CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")


class BlueskyCollector(BaseCollector):
    """Collects financial posts from Bluesky search API."""

    source_name = "BLUESKY"

    def __init__(self):
        super().__init__()
        self._handle = os.environ.get("BLUESKY_HANDLE", "")
        self._app_password = os.environ.get("BLUESKY_APP_PASSWORD", "")
        terms_env = os.environ.get("BLUESKY_SEARCH_TERMS", DEFAULT_SEARCH_TERMS)
        self._search_terms = [t.strip() for t in terms_env.split(",") if t.strip()]
        self._limit = int(os.environ.get("BLUESKY_SEARCH_LIMIT", "30"))
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "SignalFFT/1.0 (financial data collector)",
        })

    def _authenticate(self) -> bool:
        """Obtain an access token via com.atproto.server.createSession."""
        if not self._handle or not self._app_password:
            logger.error(
                "BLUESKY_HANDLE and BLUESKY_APP_PASSWORD not set — "
                "skipping Bluesky collection"
            )
            return False

        try:
            response = requests.post(
                BLUESKY_CREATE_SESSION_URL,
                json={
                    "identifier": self._handle,
                    "password": self._app_password,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            access_jwt = data.get("accessJwt")
            if not access_jwt:
                logger.error("Bluesky session response missing accessJwt")
                return False
            self._session.headers["Authorization"] = f"Bearer {access_jwt}"
            logger.info("Bluesky authenticated as %s", self._handle)
            return True
        except requests.RequestException:
            logger.exception("Failed to create Bluesky session")
            return False

    def collect(self) -> list[dict[str, Any]]:
        """Authenticate and search Bluesky for financial posts.

        Issues one search per term in BLUESKY_SEARCH_TERMS, deduplicates
        by post URI, and returns normalized post dicts.
        """
        if not self._authenticate():
            return []

        seen_uris: set[str] = set()
        all_docs: list[dict[str, Any]] = []

        for term in self._search_terms:
            try:
                response = self._session.get(
                    BLUESKY_SEARCH_URL,
                    params={
                        "q": term,
                        "sort": "latest",
                        "limit": min(self._limit, 100),
                    },
                    timeout=30,
                )
                if response.status_code == 429:
                    logger.warning("Bluesky rate limit hit (429) on query '%s'", term)
                    continue
                response.raise_for_status()
                data = response.json()
                posts = data.get("posts", [])
                if not isinstance(posts, list):
                    continue

                for post in posts:
                    uri = post.get("uri", "")
                    if uri in seen_uris:
                        continue
                    seen_uris.add(uri)

                    record = post.get("record", {})
                    text = record.get("text", "")
                    author = post.get("author", {})
                    symbols = CASHTAG_RE.findall(text)

                    doc = {
                        "bluesky_uri": uri,
                        "body": text,
                        "symbols": symbols,
                        "author_handle": author.get("handle", ""),
                        "author_display_name": author.get("displayName", ""),
                        "created_at": record.get("createdAt", ""),
                        "like_count": post.get("likeCount", 0),
                        "repost_count": post.get("repostCount", 0),
                        "reply_count": post.get("replyCount", 0),
                    }
                    all_docs.append(doc)
            except requests.RequestException:
                logger.exception("Failed to fetch Bluesky posts for query '%s'", term)

        logger.info("Bluesky: fetched %d posts from %d queries", len(all_docs), len(self._search_terms))
        return all_docs

    def extract_entity_id(self, doc: dict[str, Any]) -> str:
        """Extract ticker from the symbols list.

        Use the first cashtag if available, otherwise "SOCIAL_GENERAL".
        """
        symbols = doc.get("symbols", [])
        if symbols:
            return symbols[0]
        return "SOCIAL_GENERAL"

    def extract_event_type(self, doc: dict[str, Any]) -> str:
        """All Bluesky posts are SOCIAL_POST events."""
        return "SOCIAL_POST"


lambda_handler = make_lambda_handler(BlueskyCollector)
