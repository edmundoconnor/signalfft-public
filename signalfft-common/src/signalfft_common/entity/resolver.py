"""
Entity ID resolver -- normalizes CIK numbers to ticker symbols.

Uses SEC's company_tickers.json as the authoritative CIK->ticker mapping.
The mapping is loaded once at startup and cached in memory.

Usage:
    resolver = EntityResolver()
    ticker = resolver.cik_to_ticker("0000320193")  # Returns "AAPL"
    ticker = resolver.cik_to_ticker("320193")       # Also returns "AAPL"
    entity_id = resolver.normalize("CIK_0000320193")  # Returns "AAPL"
    entity_id = resolver.normalize("AAPL")             # Returns "AAPL" (passthrough)
    entity_id = resolver.normalize("MARKET_GENERAL")   # Returns "MARKET_GENERAL" (passthrough)
"""

import json
import logging
import os
import re
from typing import Optional

import boto3
import requests

logger = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "SignalFFT/1.0 (ed@signalfft.com)"

CACHE_BUCKET = os.environ.get("ARTIFACTS_BUCKET", os.environ.get("ARTIFACT_BUCKET", ""))
CACHE_KEY = "config/cik_ticker_mapping.json"

_PASSTHROUGH_IDS = {"MARKET_GENERAL", "SOCIAL_GENERAL", "CIK_UNKNOWN", "UNKNOWN"}
_CIK_PATTERN = re.compile(r"^(CIK_?)?\d+$")


class EntityResolver:
    """Resolves CIK numbers to ticker symbols.

    Loading strategy:
    1. Try to load from S3 cache (fast, no external dependency)
    2. If S3 cache missing, fetch from SEC and upload to S3
    3. If both fail, log warning and operate in passthrough mode

    The mapping is a dict: {"320193": "AAPL", "789019": "MSFT", ...}
    CIK keys are stored WITHOUT leading zeros for consistent lookup.
    """

    def __init__(self, preload: bool = True):
        self._cik_to_ticker: dict[str, str] = {}
        self._ticker_to_cik: dict[str, str] = {}
        self._loaded = False
        if preload:
            self._load()

    def _load(self) -> None:
        """Load the CIK->ticker mapping from S3 cache or SEC API."""
        if self._loaded:
            return

        # Try S3 cache first
        mapping = self._load_from_s3()
        if mapping:
            self._cik_to_ticker = mapping
            self._ticker_to_cik = {v: k for k, v in mapping.items()}
            self._loaded = True
            logger.info("Loaded %d CIK->ticker mappings from S3 cache", len(mapping))
            return

        # Fetch from SEC
        mapping = self._fetch_from_sec()
        if mapping:
            self._cik_to_ticker = mapping
            self._ticker_to_cik = {v: k for k, v in mapping.items()}
            self._loaded = True
            logger.info("Loaded %d CIK->ticker mappings from SEC", len(mapping))
            self._save_to_s3(mapping)
            return

        logger.warning("Failed to load CIK->ticker mapping -- resolver in passthrough mode")

    def _fetch_from_sec(self) -> Optional[dict[str, str]]:
        """Fetch company_tickers.json from SEC."""
        try:
            resp = requests.get(
                SEC_TICKERS_URL,
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            mapping: dict[str, str] = {}
            for entry in data.values():
                cik = str(entry.get("cik_str", "")).lstrip("0") or "0"
                ticker = str(entry.get("ticker", "")).upper().strip()
                if cik and ticker:
                    mapping[cik] = ticker

            return mapping if mapping else None
        except Exception:
            logger.exception("Failed to fetch CIK->ticker mapping from SEC")
            return None

    def _load_from_s3(self) -> Optional[dict[str, str]]:
        """Try to load cached mapping from S3."""
        if not CACHE_BUCKET:
            return None
        try:
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            resp = s3.get_object(Bucket=CACHE_BUCKET, Key=CACHE_KEY)
            body = resp["Body"].read().decode("utf-8")
            mapping = json.loads(body)
            return mapping if isinstance(mapping, dict) and mapping else None
        except Exception:
            logger.debug("S3 cache miss for CIK->ticker mapping")
            return None

    def _save_to_s3(self, mapping: dict[str, str]) -> None:
        """Save mapping to S3 for caching."""
        if not CACHE_BUCKET:
            return
        try:
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            s3.put_object(
                Bucket=CACHE_BUCKET,
                Key=CACHE_KEY,
                Body=json.dumps(mapping).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info("Cached CIK->ticker mapping to s3://%s/%s", CACHE_BUCKET, CACHE_KEY)
        except Exception:
            logger.warning("Failed to cache CIK->ticker mapping to S3")

    def cik_to_ticker(self, cik: str) -> Optional[str]:
        """Resolve a CIK number to a ticker symbol.

        Strips leading zeros and CIK_ prefix before lookup.
        Returns ticker string or None if not found.
        """
        if not self._loaded:
            self._load()

        cleaned = cik.removeprefix("CIK_").removeprefix("CIK").lstrip("0") or "0"
        return self._cik_to_ticker.get(cleaned)

    def ticker_to_cik(self, ticker: str) -> Optional[str]:
        """Reverse lookup: ticker -> CIK."""
        if not self._loaded:
            self._load()
        return self._ticker_to_cik.get(ticker.upper())

    def normalize(self, entity_id: str) -> str:
        """Normalize any entity ID to its canonical form (ticker symbol).

        Rules:
        1. If entity_id is MARKET_GENERAL, SOCIAL_GENERAL, etc -> return as-is
        2. If entity_id starts with CIK_ or is all digits -> resolve CIK -> ticker
        3. If already a ticker (1-5 uppercase letters) -> return as-is
        4. If resolution fails -> return original entity_id (don't lose data)
        """
        if not entity_id or entity_id in _PASSTHROUGH_IDS:
            return entity_id

        # Check if it looks like a CIK
        if _CIK_PATTERN.match(entity_id):
            ticker = self.cik_to_ticker(entity_id)
            if ticker:
                return ticker
            # Couldn't resolve — return original
            return entity_id

        # Already a ticker or other non-CIK identifier
        return entity_id

    @property
    def mapping_size(self) -> int:
        """Number of CIK->ticker mappings loaded."""
        return len(self._cik_to_ticker)
