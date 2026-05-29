"""Filing context enrichment — timing metadata for triage.

Pure functions (except has_press_release which queries DynamoDB).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from zoneinfo import ZoneInfo


_ET = ZoneInfo("America/New_York")

# US market holidays 2025-2026 (observed dates)
_MARKET_HOLIDAYS: frozenset[date] = frozenset({
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 20),   # MLK Day
    date(2025, 2, 17),   # Presidents Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


def enrich_filing_context(
    filing_date: str,
    filing_time: str | None,
    form_type: str,
) -> dict[str, Any]:
    """Compute timing metadata for a filing.

    Parameters
    ----------
    filing_date : str
        ISO date string (YYYY-MM-DD).
    filing_time : str or None
        ISO datetime string with timezone, or None if unknown.
    form_type : str
        SEC form type (e.g. "10-K", "8-K", "10-K/A").

    Returns
    -------
    dict with keys: is_after_hours, is_friday, is_holiday_adjacent,
    is_amended, filing_time_context.
    """
    d = _parse_date(filing_date)
    is_after = _is_after_hours(filing_time)
    is_fri = d.weekday() == 4 if d else False
    is_holiday_adj = _is_holiday_adjacent(d) if d else False
    is_amended = form_type.rstrip().upper().endswith("/A")

    context_parts = []
    if is_after:
        context_parts.append("filed after market hours")
    if is_fri:
        context_parts.append("on a Friday")
    if is_holiday_adj:
        context_parts.append("adjacent to a market holiday")
    if is_amended:
        context_parts.append("amended filing")
    if not context_parts:
        context_parts.append("filed during normal business hours")

    filing_time_context = "Filing was " + ", ".join(context_parts) + "."

    return {
        "is_after_hours": is_after,
        "is_friday": is_fri,
        "is_holiday_adjacent": is_holiday_adj,
        "is_amended": is_amended,
        "filing_time_context": filing_time_context,
    }


def check_press_release(
    entity_id: str,
    filing_date: str,
    events_table: Any,
) -> bool:
    """Check if a related 8-K press release was filed within +/-1 day.

    Queries the events DynamoDB table for the same entity with form_type
    containing "8-K" and filing_date within 1 day.

    This is the only non-pure function in this module.
    """
    d = _parse_date(filing_date)
    if d is None:
        return False

    day_before = (d - timedelta(days=1)).isoformat()
    day_after = (d + timedelta(days=1)).isoformat()

    pk = f"ENTITY#{entity_id}"

    try:
        response = events_table.query(
            KeyConditionExpression="PK = :pk AND SK BETWEEN :sk_start AND :sk_end",
            ExpressionAttributeValues={
                ":pk": pk,
                ":sk_start": f"SECTIONS#8-K#{day_before}",
                ":sk_end": f"SECTIONS#8-K#{day_after}z",
            },
        )
        return response.get("Count", 0) > 0
    except Exception:
        return False


def _parse_date(filing_date: str) -> date | None:
    """Parse an ISO date string, returning None on failure."""
    try:
        return date.fromisoformat(filing_date)
    except (ValueError, TypeError):
        return None


def _is_after_hours(filing_time: str | None) -> bool:
    """Check if filing time is after 4 PM ET (market close)."""
    if not filing_time:
        return False
    try:
        dt = datetime.fromisoformat(filing_time)
        # Convert to ET
        dt_et = dt.astimezone(_ET)
        return dt_et.hour >= 16
    except (ValueError, TypeError):
        return False


def _is_holiday_adjacent(d: date) -> bool:
    """Check if date is within 1 business day of a market holiday."""
    for offset in (-1, 0, 1):
        check = d + timedelta(days=offset)
        if check in _MARKET_HOLIDAYS:
            return True
    return False
