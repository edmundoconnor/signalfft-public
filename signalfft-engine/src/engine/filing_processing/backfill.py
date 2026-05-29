"""SEC filing history backfill via the Submissions API.

Pure functions — no DynamoDB, no S3, no SQS. Fetches the list of filings
for a given CIK and form type from the SEC EDGAR Submissions endpoint.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# SEC asks callers to stay under 10 requests per second.
_SEC_REQUEST_INTERVAL = 0.2  # 200ms between requests


def _pad_cik(cik: str) -> str:
    """Pad CIK to 10 digits with leading zeros."""
    return cik.lstrip("0").zfill(10)


def fetch_filing_history(
    cik: str,
    form_type: str,
    user_agent: str,
) -> list[dict]:
    """Fetch all filings of *form_type* for *cik* from the SEC Submissions API.

    Returns a list of dicts sorted ascending by ``filing_date``::

        [{"accession_number": "...", "filing_date": "YYYY-MM-DD", "form_type": "10-K"}, ...]

    Returns an empty list on any failure (logs a warning).
    """
    padded = _pad_cik(cik)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"

    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    try:
        time.sleep(_SEC_REQUEST_INTERVAL)
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Failed to fetch SEC submissions for CIK %s", cik, exc_info=True)
        return []

    try:
        data = response.json()
    except ValueError:
        logger.warning("Invalid JSON from SEC submissions for CIK %s", cik)
        return []

    recent = data.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    forms = recent.get("form", [])

    if not accession_numbers:
        return []

    results: list[dict] = []
    for acc, date, form in zip(accession_numbers, filing_dates, forms):
        if form == form_type:
            results.append({
                "accession_number": acc,
                "filing_date": date,
                "form_type": form,
            })

    # Sort ascending by filing_date
    results.sort(key=lambda r: r["filing_date"])
    return results
