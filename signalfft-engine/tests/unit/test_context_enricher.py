"""Tests for quiet filing triage context enrichment."""

from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import MagicMock

from engine.ai_edges.quiet_filing_triage.context_enricher import (
    enrich_filing_context,
    check_press_release,
    _is_after_hours,
    _is_holiday_adjacent,
    _parse_date,
)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_valid_iso_date(self):
        assert _parse_date("2026-02-25") == date(2026, 2, 25)

    def test_invalid_string(self):
        assert _parse_date("not-a-date") is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none_input(self):
        assert _parse_date(None) is None


# ---------------------------------------------------------------------------
# _is_after_hours
# ---------------------------------------------------------------------------

class TestIsAfterHours:
    def test_none_time(self):
        assert _is_after_hours(None) is False

    def test_empty_string(self):
        assert _is_after_hours("") is False

    def test_before_close_et(self):
        # 3 PM ET = within market hours
        assert _is_after_hours("2026-02-25T15:00:00-05:00") is False

    def test_at_close_et(self):
        # 4 PM ET = after hours
        assert _is_after_hours("2026-02-25T16:00:00-05:00") is True

    def test_after_close_et(self):
        # 6 PM ET
        assert _is_after_hours("2026-02-25T18:00:00-05:00") is True

    def test_utc_before_close_et(self):
        # 20:00 UTC = 3 PM ET (EST -5)
        assert _is_after_hours("2026-02-25T20:00:00+00:00") is False

    def test_utc_after_close_et(self):
        # 22:00 UTC = 5 PM ET (EST -5)
        assert _is_after_hours("2026-02-25T22:00:00+00:00") is True

    def test_invalid_time_string(self):
        assert _is_after_hours("invalid") is False


# ---------------------------------------------------------------------------
# _is_holiday_adjacent
# ---------------------------------------------------------------------------

class TestIsHolidayAdjacent:
    def test_on_holiday(self):
        # Christmas 2025
        assert _is_holiday_adjacent(date(2025, 12, 25)) is True

    def test_day_before_holiday(self):
        # Dec 24 = one day before Christmas
        assert _is_holiday_adjacent(date(2025, 12, 24)) is True

    def test_day_after_holiday(self):
        # Dec 26 = one day after Christmas
        assert _is_holiday_adjacent(date(2025, 12, 26)) is True

    def test_not_near_holiday(self):
        # Feb 10, 2026 — not near any holiday
        assert _is_holiday_adjacent(date(2026, 2, 10)) is False

    def test_two_days_before_holiday(self):
        # Dec 23 = two days before Christmas, not adjacent
        assert _is_holiday_adjacent(date(2025, 12, 23)) is False


# ---------------------------------------------------------------------------
# enrich_filing_context
# ---------------------------------------------------------------------------

class TestEnrichFilingContext:
    def test_normal_business_hours(self):
        # A Tuesday, not near a holiday, not amended
        ctx = enrich_filing_context("2026-02-24", None, "10-K")
        assert ctx["is_after_hours"] is False
        assert ctx["is_friday"] is False
        assert ctx["is_holiday_adjacent"] is False
        assert ctx["is_amended"] is False
        assert "normal business hours" in ctx["filing_time_context"]

    def test_friday_filing(self):
        # 2026-02-27 is a Friday
        ctx = enrich_filing_context("2026-02-27", None, "10-Q")
        assert ctx["is_friday"] is True
        assert "Friday" in ctx["filing_time_context"]

    def test_after_hours_filing(self):
        ctx = enrich_filing_context(
            "2026-02-25", "2026-02-25T18:00:00-05:00", "8-K",
        )
        assert ctx["is_after_hours"] is True
        assert "after market hours" in ctx["filing_time_context"]

    def test_holiday_adjacent_filing(self):
        # Thanksgiving 2026 = Nov 26; Nov 25 is adjacent
        ctx = enrich_filing_context("2026-11-25", None, "10-K")
        assert ctx["is_holiday_adjacent"] is True
        assert "holiday" in ctx["filing_time_context"]

    def test_amended_filing(self):
        ctx = enrich_filing_context("2026-02-25", None, "10-K/A")
        assert ctx["is_amended"] is True
        assert "amended" in ctx["filing_time_context"]

    def test_amended_lowercase(self):
        ctx = enrich_filing_context("2026-02-25", None, "10-k/a")
        assert ctx["is_amended"] is True

    def test_multiple_flags_combine(self):
        # Friday + after hours + holiday adjacent
        # 2025-07-04 is a Friday and a holiday
        ctx = enrich_filing_context(
            "2025-07-04", "2025-07-04T18:00:00-04:00", "8-K/A",
        )
        assert ctx["is_after_hours"] is True
        assert ctx["is_friday"] is True
        assert ctx["is_holiday_adjacent"] is True
        assert ctx["is_amended"] is True
        filing_ctx = ctx["filing_time_context"]
        assert "after market hours" in filing_ctx
        assert "Friday" in filing_ctx
        assert "holiday" in filing_ctx
        assert "amended" in filing_ctx

    def test_invalid_date_returns_safe_defaults(self):
        ctx = enrich_filing_context("invalid", None, "10-K")
        assert ctx["is_friday"] is False
        assert ctx["is_holiday_adjacent"] is False


# ---------------------------------------------------------------------------
# check_press_release
# ---------------------------------------------------------------------------

class TestCheckPressRelease:
    def _make_table(self, count: int = 0):
        table = MagicMock()
        table.query.return_value = {"Count": count}
        return table

    def test_no_press_release(self):
        table = self._make_table(0)
        result = check_press_release("AAPL", "2026-02-25", table)
        assert result is False
        table.query.assert_called_once()

    def test_has_press_release(self):
        table = self._make_table(1)
        result = check_press_release("AAPL", "2026-02-25", table)
        assert result is True

    def test_invalid_date_returns_false(self):
        table = self._make_table()
        assert check_press_release("AAPL", "invalid", table) is False
        table.query.assert_not_called()

    def test_query_exception_returns_false(self):
        table = MagicMock()
        table.query.side_effect = Exception("DynamoDB error")
        assert check_press_release("AAPL", "2026-02-25", table) is False

    def test_query_uses_correct_key_range(self):
        table = self._make_table(0)
        check_press_release("BSX", "2026-03-15", table)

        call_kwargs = table.query.call_args[1]
        vals = call_kwargs["ExpressionAttributeValues"]
        assert vals[":pk"] == "ENTITY#BSX"
        assert vals[":sk_start"] == "SECTIONS#8-K#2026-03-14"
        assert vals[":sk_end"] == "SECTIONS#8-K#2026-03-16z"
