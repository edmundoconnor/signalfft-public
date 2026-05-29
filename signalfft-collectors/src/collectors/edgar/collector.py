"""SEC EDGAR filing collector for SignalFFT."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import re
import requests

from collectors.base import BaseCollector, make_lambda_handler
from signalfft_common.entity import EntityResolver

logger = logging.getLogger(__name__)

# EDGAR full-text search API (EFTS)
EDGAR_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FILINGS_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions"

# Filing types we care about
FILING_TYPES = ("10-K", "10-Q", "8-K", "S-1", "DEF 14A", "13F-HR")


class EdgarCollector(BaseCollector):
    """Collects SEC filings from EDGAR EFTS API."""

    source_name = "SEC_EDGAR"

    def __init__(self):
        super().__init__()
        self._resolver = EntityResolver()
        self._user_agent = os.environ.get(
            "EDGAR_USER_AGENT",
            "SignalFFT/0.1 (contact@example.com)",
        )
        self._filing_types = os.environ.get(
            "EDGAR_FILING_TYPES",
            ",".join(FILING_TYPES),
        ).split(",")
        self._max_filings = int(os.environ.get("EDGAR_MAX_FILINGS", "50"))
        self._filing_fetch_queue_url = os.environ.get("FILING_FETCH_QUEUE_URL", "")
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        })

    def collect(self) -> list[dict[str, Any]]:
        """Fetch recent SEC filings from EDGAR full-text search API."""
        filings: list[dict[str, Any]] = []

        for form_type in self._filing_types:
            try:
                batch = self._fetch_filings_by_type(form_type.strip())
                filings.extend(batch)
            except Exception:
                logger.exception("Failed to fetch %s filings", form_type)

        logger.info("Collected %d total filings from EDGAR", len(filings))
        return filings[: self._max_filings]

    def _fetch_filings_by_type(self, form_type: str) -> list[dict[str, Any]]:
        """Fetch filings of a specific form type from EDGAR EFTS."""
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "dateRange": "custom",
            "startdt": self._get_lookback_date(),
            "enddt": self._get_today(),
            "forms": form_type,
        }

        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        results: list[dict[str, Any]] = []
        for hit in data.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})

            # CIK is in "ciks" array
            ciks = source.get("ciks", [])
            cik = ciks[0] if ciks else ""

            # Company name and ticker from display_names
            display_names = source.get("display_names", [])
            company_name = display_names[0] if display_names else ""
            ticker = ""
            if company_name:
                # Match first ticker in (AAPL) or (NWS, NWSA, NWSLL)
                # Won't match (CIK ...) since CIK is followed by a space
                ticker_match = re.search(r"\(([A-Z]{1,5})[,)]", company_name)
                if ticker_match:
                    ticker = ticker_match.group(1)

            # Accession number from "adsh"
            adsh = source.get("adsh", "")
            filing_url = ""
            if adsh and cik:
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{adsh.replace('-', '')}/{adsh}-index.htm"

            filing = {
                "accession_number": adsh,
                "form_type": source.get("form", source.get("file_type", form_type)),
                "company_name": company_name,
                "ticker": ticker,
                "cik": cik,
                "filed_date": source.get("file_date", ""),
                "filing_url": filing_url,
                "description": source.get("file_description", ""),
            }
            results.append(filing)
        return results

    def _get_lookback_date(self) -> str:
        """Return date string for lookback window (default 1 day)."""
        from datetime import datetime, timedelta, timezone

        lookback_days = int(os.environ.get("EDGAR_LOOKBACK_DAYS", "1"))
        dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        return dt.strftime("%Y-%m-%d")

    def _get_today(self) -> str:
        """Return today's date as a string."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def extract_entity_id(self, doc: dict[str, Any]) -> str:
        """Use ticker as entity ID, resolving CIK via SEC mapping if needed."""
        ticker = doc.get("ticker", "")
        if ticker:
            return ticker
        cik = doc.get("cik", "")
        if cik:
            resolved = self._resolver.cik_to_ticker(str(cik))
            if resolved:
                return resolved
            return f"CIK_{cik}"
        return "CIK_UNKNOWN"

    def extract_event_type(self, doc: dict[str, Any]) -> str:
        """Map form type to event type string."""
        form_type = doc.get("form_type", "UNKNOWN")
        # Normalize: "10-K" -> "SEC_10K", "8-K" -> "SEC_8K"
        normalized = form_type.replace("-", "").replace(" ", "_").upper()
        return f"SEC_{normalized}"

    def on_event_stored(self, event_id: str, entity_id: str, doc: dict[str, Any]) -> None:
        """Publish FilingDocumentRequested to the filing-fetch queue."""
        if not self._filing_fetch_queue_url:
            return
        filing_url = doc.get("filing_url", "")
        if not filing_url:
            return

        from signalfft_common.events import FilingDocumentRequested

        event = FilingDocumentRequested(
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=self.source_name,
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "filing_url": filing_url,
                "form_type": doc.get("form_type", ""),
                "filing_date": doc.get("filed_date", ""),
                "cik": doc.get("cik", ""),
            },
        )
        self._sqs.send_message(
            QueueUrl=self._filing_fetch_queue_url,
            MessageBody=event.to_sqs_message(),
        )
        logger.info("Published FilingDocumentRequested for %s", event_id)


# Lambda handler
lambda_handler = make_lambda_handler(EdgarCollector)
