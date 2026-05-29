"""Tests for the CIK->ticker entity resolver."""

from unittest.mock import patch, MagicMock

import pytest

from signalfft_common.entity.resolver import EntityResolver


MOCK_SEC_DATA = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "3": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla Inc"},
    "4": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
}


@pytest.fixture
def resolver():
    """Create an EntityResolver with mocked SEC data."""
    with patch("signalfft_common.entity.resolver.EntityResolver._load_from_s3", return_value=None), \
         patch("signalfft_common.entity.resolver.EntityResolver._save_to_s3"), \
         patch("signalfft_common.entity.resolver.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SEC_DATA
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        r = EntityResolver(preload=True)
        assert r.mapping_size == 5
        yield r


class TestCikToTicker:
    def test_known_cik(self, resolver):
        assert resolver.cik_to_ticker("0000320193") == "AAPL"

    def test_cik_without_leading_zeros(self, resolver):
        assert resolver.cik_to_ticker("320193") == "AAPL"

    def test_cik_with_prefix(self, resolver):
        assert resolver.cik_to_ticker("CIK_0000320193") == "AAPL"

    def test_cik_with_bare_prefix(self, resolver):
        assert resolver.cik_to_ticker("CIK320193") == "AAPL"

    def test_unknown_cik(self, resolver):
        assert resolver.cik_to_ticker("9999999999") is None

    def test_microsoft(self, resolver):
        assert resolver.cik_to_ticker("789019") == "MSFT"


class TestTickerToCik:
    def test_reverse_lookup(self, resolver):
        assert resolver.ticker_to_cik("AAPL") == "320193"

    def test_reverse_lookup_case_insensitive(self, resolver):
        assert resolver.ticker_to_cik("aapl") == "320193"

    def test_unknown_ticker(self, resolver):
        assert resolver.ticker_to_cik("ZZZZZ") is None


class TestNormalize:
    def test_cik_prefix(self, resolver):
        assert resolver.normalize("CIK_0000320193") == "AAPL"

    def test_bare_digits(self, resolver):
        assert resolver.normalize("320193") == "AAPL"

    def test_ticker_passthrough(self, resolver):
        assert resolver.normalize("AAPL") == "AAPL"

    def test_market_general_passthrough(self, resolver):
        assert resolver.normalize("MARKET_GENERAL") == "MARKET_GENERAL"

    def test_social_general_passthrough(self, resolver):
        assert resolver.normalize("SOCIAL_GENERAL") == "SOCIAL_GENERAL"

    def test_unknown_cik_returns_original(self, resolver):
        assert resolver.normalize("CIK_9999999999") == "CIK_9999999999"

    def test_empty_string(self, resolver):
        assert resolver.normalize("") == ""

    def test_cik_unknown_passthrough(self, resolver):
        assert resolver.normalize("CIK_UNKNOWN") == "CIK_UNKNOWN"

    def test_multi_letter_ticker_passthrough(self, resolver):
        assert resolver.normalize("GOOGL") == "GOOGL"


class TestLoadFailure:
    def test_passthrough_on_load_failure(self):
        """If SEC fetch and S3 both fail, normalize returns input unchanged."""
        with patch("signalfft_common.entity.resolver.EntityResolver._load_from_s3", return_value=None), \
             patch("signalfft_common.entity.resolver.EntityResolver._save_to_s3"), \
             patch("signalfft_common.entity.resolver.requests.get", side_effect=Exception("network error")):
            r = EntityResolver(preload=True)
            assert r.mapping_size == 0
            # Should pass through without error
            assert r.normalize("CIK_0000320193") == "CIK_0000320193"
            assert r.normalize("AAPL") == "AAPL"
            assert r.normalize("MARKET_GENERAL") == "MARKET_GENERAL"

    def test_lazy_load(self):
        """With preload=False, mapping loads on first use."""
        with patch("signalfft_common.entity.resolver.EntityResolver._load_from_s3", return_value=None), \
             patch("signalfft_common.entity.resolver.EntityResolver._save_to_s3"), \
             patch("signalfft_common.entity.resolver.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = MOCK_SEC_DATA
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            r = EntityResolver(preload=False)
            assert r.mapping_size == 0  # Not loaded yet
            assert r.cik_to_ticker("320193") == "AAPL"  # Triggers load
            assert r.mapping_size == 5


class TestS3Cache:
    def test_loads_from_s3_cache(self):
        """When S3 cache exists, use it without hitting SEC."""
        cached = {"320193": "AAPL", "789019": "MSFT"}
        with patch("signalfft_common.entity.resolver.EntityResolver._load_from_s3", return_value=cached), \
             patch("signalfft_common.entity.resolver.requests.get") as mock_get:
            r = EntityResolver(preload=True)
            assert r.mapping_size == 2
            assert r.cik_to_ticker("320193") == "AAPL"
            mock_get.assert_not_called()  # SEC not contacted
