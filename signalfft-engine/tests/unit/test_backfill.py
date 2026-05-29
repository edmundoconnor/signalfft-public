"""Unit tests for SEC filing history backfill (pure functions)."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from engine.filing_processing.backfill import _pad_cik, fetch_filing_history


# ---------------------------------------------------------------------------
# _pad_cik
# ---------------------------------------------------------------------------


class TestPadCik:
    def test_short_cik(self) -> None:
        assert _pad_cik("320193") == "0000320193"

    def test_already_padded(self) -> None:
        assert _pad_cik("0000320193") == "0000320193"

    def test_single_digit(self) -> None:
        assert _pad_cik("1") == "0000000001"

    def test_leading_zeros_stripped_then_repadded(self) -> None:
        assert _pad_cik("00123") == "0000000123"

    def test_ten_digit_cik(self) -> None:
        assert _pad_cik("1234567890") == "1234567890"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_SUBMISSIONS = {
    "cik": "320193",
    "entityType": "operating",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-26-000012",
                "0000320193-25-000045",
                "0000320193-25-000010",
                "0000320193-24-000033",
                "0000320193-24-000015",
            ],
            "filingDate": [
                "2026-02-15",
                "2025-11-01",
                "2025-02-14",
                "2024-11-03",
                "2024-02-15",
            ],
            "form": [
                "10-K",
                "10-Q",
                "10-K",
                "10-Q",
                "10-K",
            ],
        },
    },
}


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(response=resp)
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# fetch_filing_history
# ---------------------------------------------------------------------------


class TestFetchFilingHistory:
    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_happy_path_10k(self, mock_get, mock_sleep) -> None:
        """Should return only 10-K filings sorted ascending by date."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")

        assert len(result) == 3
        assert result[0]["filing_date"] == "2024-02-15"
        assert result[1]["filing_date"] == "2025-02-14"
        assert result[2]["filing_date"] == "2026-02-15"
        for r in result:
            assert r["form_type"] == "10-K"
            assert "accession_number" in r

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_happy_path_10q(self, mock_get, mock_sleep) -> None:
        """Should return only 10-Q filings when requested."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        result = fetch_filing_history("320193", "10-Q", "TestAgent/1.0")

        assert len(result) == 2
        assert all(r["form_type"] == "10-Q" for r in result)

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_form_type_exact_match(self, mock_get, mock_sleep) -> None:
        """Form type filtering should be exact (not substring) match."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        result = fetch_filing_history("320193", "10", "TestAgent/1.0")
        assert len(result) == 0

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_no_matching_form_type(self, mock_get, mock_sleep) -> None:
        """Should return empty list when no filings match form_type."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        result = fetch_filing_history("320193", "8-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_empty_filings(self, mock_get, mock_sleep) -> None:
        """Should return empty list when no filings exist."""
        mock_get.return_value = _mock_response(200, {
            "filings": {"recent": {"accessionNumber": [], "filingDate": [], "form": []}},
        })

        result = fetch_filing_history("999999", "10-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_missing_filings_key(self, mock_get, mock_sleep) -> None:
        """Should handle missing 'filings' key gracefully."""
        mock_get.return_value = _mock_response(200, {"cik": "320193"})

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_http_error_returns_empty(self, mock_get, mock_sleep) -> None:
        """Should return empty list on HTTP errors."""
        mock_get.return_value = _mock_response(404)

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_connection_error_returns_empty(self, mock_get, mock_sleep) -> None:
        """Should return empty list on connection errors."""
        from requests.exceptions import ConnectionError
        mock_get.side_effect = ConnectionError("Connection refused")

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_invalid_json_returns_empty(self, mock_get, mock_sleep) -> None:
        """Should return empty list on invalid JSON responses."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = resp

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")
        assert result == []

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_correct_url_construction(self, mock_get, mock_sleep) -> None:
        """Should build correct SEC URL with padded CIK."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        fetch_filing_history("320193", "10-K", "TestAgent/1.0")

        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert url == "https://data.sec.gov/submissions/CIK0000320193.json"

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_user_agent_header(self, mock_get, mock_sleep) -> None:
        """Should pass user-agent header."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        fetch_filing_history("320193", "10-K", "MyApp/2.0")

        headers = mock_get.call_args[1]["headers"]
        assert headers["User-Agent"] == "MyApp/2.0"

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_rate_limit_sleep(self, mock_get, mock_sleep) -> None:
        """Should sleep before each request for rate limiting."""
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        fetch_filing_history("320193", "10-K", "TestAgent/1.0")

        mock_sleep.assert_called_once_with(0.2)

    @patch("engine.filing_processing.backfill.time.sleep")
    @patch("engine.filing_processing.backfill.requests.get")
    def test_results_sorted_ascending(self, mock_get, mock_sleep) -> None:
        """Results should always be sorted ascending by filing_date."""
        # Give data in descending order
        mock_get.return_value = _mock_response(200, SAMPLE_SUBMISSIONS)

        result = fetch_filing_history("320193", "10-K", "TestAgent/1.0")
        dates = [r["filing_date"] for r in result]
        assert dates == sorted(dates)
