#!/usr/bin/env python3
"""
SignalFFT Validation Report — Edge 1 + Outcome Analysis
========================================================

Standalone script. Run locally with AWS credentials configured.
Pulls outcome, signal, and triage data from DynamoDB, cross-references,
and outputs a validation report answering:

  1. Do signals correlate with price movement at all? (baseline)
  2. Does directional prediction have accuracy? (F2 validation)
  3. Do Edge 1 triage-boosted signals outperform? (Edge 1 validation)
  4. What's the liquidity profile of signaled entities?

Usage:
    python3 signalfft_validation_report.py

Outputs:
    - Terminal: formatted report
    - validation_report.csv: raw data for manual inspection
    - validation_summary.csv: aggregated statistics

Requirements:
    pip install boto3 --break-system-packages
"""

import boto3
import json
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean, median, stdev

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REGION = "us-east-1"
TABLE_PREFIX = "prod-signalfft"

OUTCOMES_TABLE = f"{TABLE_PREFIX}-outcomes"
SIGNALS_TABLE = f"{TABLE_PREFIX}-signals"
EVENTS_TABLE = f"{TABLE_PREFIX}-events"
SHADOW_TABLE = f"{TABLE_PREFIX}-shadow-scores"

# Minimum ADDV to consider a stock tradeable (from v2 roadmap)
MIN_ADDV = 100_000  # $100K

# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------
dynamo = boto3.resource("dynamodb", region_name=REGION)


def scan_all(table_name, filter_expression=None, expression_values=None,
             expression_names=None, limit=None):
    """Full table scan with pagination. Returns list of items."""
    table = dynamo.Table(table_name)
    kwargs = {}
    if filter_expression:
        kwargs["FilterExpression"] = filter_expression
    if expression_values:
        kwargs["ExpressionAttributeValues"] = expression_values
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names

    items = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if limit and len(items) >= limit:
            items = items[:limit]
            break
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        # Progress indicator
        print(f"  ... scanned {len(items)} records", end="\r")

    print(f"  ... scanned {len(items)} records (done)")
    return items


def to_float(val, default=None):
    """Safely convert Decimal/string/None to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_outcomes():
    """Load all outcome records with at least T+1d price data."""
    print(f"\n📊 Loading outcomes from {OUTCOMES_TABLE}...")
    items = scan_all(OUTCOMES_TABLE)

    outcomes = []
    for item in items:
        price_t1d = to_float(item.get("price_t1d"))
        if price_t1d is None:
            continue  # skip outcomes without T+1d data

        outcomes.append({
            "entity_id": item.get("PK", "").replace("ENTITY#", ""),
            "signal_id": item.get("SK", "").replace("OUTCOME#", ""),
            "ticker": item.get("ticker", ""),
            "signal_score": to_float(item.get("signal_score"), 0),
            "direction_score": to_float(item.get("direction_score"), 0),
            "price_at_signal": to_float(item.get("price_at_signal")),
            "price_t1d": price_t1d,
            "price_t5d": to_float(item.get("price_t5d")),
            "raw_pct_change_t1d": to_float(item.get("raw_pct_change_t1d")),
            "raw_pct_change_t5d": to_float(item.get("raw_pct_change_t5d")),
            "spread_adj_pct_change_t1d": to_float(item.get("spread_adj_pct_change_t1d")),
            "spread_adj_pct_change_t5d": to_float(item.get("spread_adj_pct_change_t5d")),
            "spread_at_signal": to_float(item.get("spread_at_signal"), 0),
            "addv_20d": to_float(item.get("addv_20d"), 0),
            "signal_timestamp": item.get("signal_timestamp", ""),
            "created_at": item.get("created_at", ""),
        })

    print(f"   Loaded {len(outcomes)} outcomes with T+1d data")
    return outcomes


def load_triage_records():
    """Load triage records from events table.

    Triage records use PK/SK patterns from the quiet_filing_triage edge.
    Scan the events table for triage-patterned keys.
    """
    print(f"\n🔍 Loading triage records from {EVENTS_TABLE}...")

    # Triage records may use various PK/SK patterns depending on implementation.
    # Try scanning for records that have materiality_score attribute.
    items = scan_all(
        EVENTS_TABLE,
        filter_expression="attribute_exists(materiality_score)",
    )

    triages = []
    for item in items:
        triages.append({
            "entity_id": item.get("PK", "").replace("ENTITY#", "").replace("TRIAGE#", ""),
            "materiality_score": to_float(item.get("materiality_score"), 0),
            "attention_likelihood": item.get("attention_likelihood", ""),
            "direction": item.get("direction", "neutral"),
            "suggested_urgency": item.get("suggested_urgency", ""),
            "signal_boost_applied": item.get("signal_boost_applied", False),
            "is_after_hours": item.get("is_after_hours", False),
            "is_friday": item.get("is_friday", False),
            "filing_date": item.get("filing_date", ""),
            "form_type": item.get("form_type", ""),
            "created_at": item.get("created_at", ""),
        })

    print(f"   Loaded {len(triages)} triage records")
    return triages


def load_shadow_scores():
    """Load shadow score records."""
    print(f"\n🌗 Loading shadow scores from {SHADOW_TABLE}...")
    try:
        items = scan_all(SHADOW_TABLE)
    except Exception as e:
        print(f"   Shadow scores table not found or empty: {e}")
        return []

    shadows = []
    for item in items:
        shadows.append({
            "entity_id": item.get("PK", "").replace("ENTITY#", ""),
            "signal_id": item.get("signal_id", ""),
            "edge_name": item.get("edge_name", ""),
            "original_score": to_float(item.get("original_score"), 0),
            "shadow_semantic_impact": to_float(item.get("shadow_semantic_impact"), 0),
            "triage_boost": to_float(item.get("triage_boost"), 0),
            "delta_impact": to_float(item.get("delta_impact"), 0),
            "direction_consensus": item.get("direction_consensus", ""),
            "max_severity": to_float(item.get("max_severity"), 0),
            "created_at": item.get("created_at", ""),
        })

    print(f"   Loaded {len(shadows)} shadow score records")
    return shadows


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------
def safe_stats(values, label=""):
    """Compute stats from a list of floats, handling edge cases."""
    values = [v for v in values if v is not None]
    if not values:
        return {"count": 0, "mean": None, "median": None, "stdev": None,
                "min": None, "max": None, "positive_pct": None}

    pos = sum(1 for v in values if v > 0)
    neg = sum(1 for v in values if v < 0)
    zero = sum(1 for v in values if v == 0)

    result = {
        "count": len(values),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "stdev": round(stdev(values), 4) if len(values) > 1 else 0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "positive_pct": round(100 * pos / len(values), 1),
        "negative_pct": round(100 * neg / len(values), 1),
    }
    return result


def analyze_baseline(outcomes):
    """Section 1: Do signals correlate with price movement at all?"""
    print("\n" + "=" * 70)
    print("SECTION 1: BASELINE — DO SIGNALS CORRELATE WITH PRICE MOVEMENT?")
    print("=" * 70)

    # Overall T+1d stats
    t1d_changes = [o["raw_pct_change_t1d"] for o in outcomes
                   if o["raw_pct_change_t1d"] is not None]
    stats_t1d = safe_stats(t1d_changes)

    print(f"\n  Total outcomes with T+1d: {stats_t1d['count']}")
    print(f"  Mean return T+1d:        {stats_t1d['mean']}%")
    print(f"  Median return T+1d:      {stats_t1d['median']}%")
    print(f"  Stdev T+1d:              {stats_t1d['stdev']}%")
    print(f"  Positive T+1d:           {stats_t1d['positive_pct']}%")
    print(f"  Negative T+1d:           {stats_t1d['negative_pct']}%")
    print(f"  Range:                   [{stats_t1d['min']}%, {stats_t1d['max']}%]")

    # T+5d stats
    t5d_changes = [o["raw_pct_change_t5d"] for o in outcomes
                   if o["raw_pct_change_t5d"] is not None]
    stats_t5d = safe_stats(t5d_changes)

    print(f"\n  Outcomes with T+5d:      {stats_t5d['count']}")
    if stats_t5d["count"] > 0:
        print(f"  Mean return T+5d:        {stats_t5d['mean']}%")
        print(f"  Median return T+5d:      {stats_t5d['median']}%")
        print(f"  Positive T+5d:           {stats_t5d['positive_pct']}%")

    # Score-bucketed analysis
    print(f"\n  --- Signal Score Bucketed ---")
    print(f"  {'Score Range':<15} {'Count':>7} {'Mean T+1d':>10} {'Med T+1d':>10} {'Pos %':>8} {'Mean T+5d':>10}")
    print(f"  {'-'*13:<15} {'-'*5:>7} {'-'*8:>10} {'-'*8:>10} {'-'*5:>8} {'-'*8:>10}")

    buckets = [
        ("0.00 - 0.20", 0.00, 0.20),
        ("0.20 - 0.40", 0.20, 0.40),
        ("0.40 - 0.60", 0.40, 0.60),
        ("0.60 - 0.80", 0.60, 0.80),
        ("0.80 - 1.00", 0.80, 1.01),
    ]

    bucket_data = {}
    for label, lo, hi in buckets:
        bucket_outcomes = [o for o in outcomes if lo <= o["signal_score"] < hi]
        t1d = [o["raw_pct_change_t1d"] for o in bucket_outcomes
               if o["raw_pct_change_t1d"] is not None]
        t5d = [o["raw_pct_change_t5d"] for o in bucket_outcomes
               if o["raw_pct_change_t5d"] is not None]
        s1 = safe_stats(t1d)
        s5 = safe_stats(t5d)
        bucket_data[label] = {"t1d": s1, "t5d": s5}

        mean_t1d = f"{s1['mean']}%" if s1['mean'] is not None else "—"
        med_t1d = f"{s1['median']}%" if s1['median'] is not None else "—"
        pos_pct = f"{s1['positive_pct']}%" if s1['positive_pct'] is not None else "—"
        mean_t5d = f"{s5['mean']}%" if s5['mean'] is not None else "—"
        print(f"  {label:<15} {s1['count']:>7} {mean_t1d:>10} {med_t1d:>10} {pos_pct:>8} {mean_t5d:>10}")

    # Key question: does higher score → better returns?
    print(f"\n  ⚡ KEY QUESTION: Does higher signal score predict better returns?")
    high_score = [o["raw_pct_change_t1d"] for o in outcomes
                  if o["signal_score"] >= 0.60 and o["raw_pct_change_t1d"] is not None]
    low_score = [o["raw_pct_change_t1d"] for o in outcomes
                 if o["signal_score"] < 0.40 and o["raw_pct_change_t1d"] is not None]
    h = safe_stats(high_score)
    l = safe_stats(low_score)
    print(f"     High score (≥0.60): n={h['count']}, mean={h['mean']}%, positive={h['positive_pct']}%")
    print(f"     Low score  (<0.40): n={l['count']}, mean={l['mean']}%, positive={l['positive_pct']}%")
    if h['mean'] is not None and l['mean'] is not None:
        diff = round(h['mean'] - l['mean'], 4)
        print(f"     Difference:         {'+' if diff > 0 else ''}{diff}% (high minus low)")
        if abs(diff) < 0.1:
            print(f"     → No meaningful difference. Signal score alone does not predict direction.")
        elif diff > 0:
            print(f"     → Higher scores show better returns. Signal has some predictive value.")
        else:
            print(f"     → Higher scores show WORSE returns. Signal may be inverted or noisy.")

    return stats_t1d, stats_t5d, bucket_data


def analyze_direction(outcomes):
    """Section 2: Does directional prediction work?"""
    print("\n" + "=" * 70)
    print("SECTION 2: DIRECTIONAL PREDICTION — DOES DIRECTION SCORE WORK?")
    print("=" * 70)

    # Segment by predicted direction
    bullish = [o for o in outcomes if o["direction_score"] and o["direction_score"] > 0.1]
    bearish = [o for o in outcomes if o["direction_score"] and o["direction_score"] < -0.1]
    neutral = [o for o in outcomes if o["direction_score"] is None
               or abs(o["direction_score"]) <= 0.1]

    print(f"\n  Predicted LONG (direction > 0.1):  {len(bullish)}")
    print(f"  Predicted SHORT (direction < -0.1): {len(bearish)}")
    print(f"  NEUTRAL (|direction| ≤ 0.1):        {len(neutral)}")

    # Bullish predictions: did price go up?
    if bullish:
        bull_t1d = [o["raw_pct_change_t1d"] for o in bullish
                    if o["raw_pct_change_t1d"] is not None]
        bull_stats = safe_stats(bull_t1d)
        print(f"\n  LONG predictions (n={bull_stats['count']}):")
        print(f"    Mean T+1d:    {bull_stats['mean']}%")
        print(f"    Positive:     {bull_stats['positive_pct']}%  {'✅' if bull_stats['positive_pct'] and bull_stats['positive_pct'] > 55 else '⚠️' if bull_stats['positive_pct'] and bull_stats['positive_pct'] > 50 else '❌'}")
        print(f"    (Random would be ~50%. Need >55% to suggest signal.)")

    # Bearish predictions: did price go down?
    if bearish:
        bear_t1d = [o["raw_pct_change_t1d"] for o in bearish
                    if o["raw_pct_change_t1d"] is not None]
        bear_stats = safe_stats(bear_t1d)
        print(f"\n  SHORT predictions (n={bear_stats['count']}):")
        print(f"    Mean T+1d:    {bear_stats['mean']}%")
        print(f"    Negative:     {bear_stats['negative_pct']}%  {'✅' if bear_stats['negative_pct'] and bear_stats['negative_pct'] > 55 else '⚠️' if bear_stats['negative_pct'] and bear_stats['negative_pct'] > 50 else '❌'}")
        print(f"    (For shorts, we want >55% negative.)")

    # Directional accuracy: did the predicted direction match?
    correct = 0
    total_directional = 0
    for o in outcomes:
        ds = o["direction_score"]
        t1d = o["raw_pct_change_t1d"]
        if ds is None or t1d is None or abs(ds) <= 0.1:
            continue
        total_directional += 1
        if (ds > 0.1 and t1d > 0) or (ds < -0.1 and t1d < 0):
            correct += 1

    if total_directional > 0:
        accuracy = round(100 * correct / total_directional, 1)
        print(f"\n  ⚡ DIRECTIONAL ACCURACY:")
        print(f"     Correct: {correct} / {total_directional} = {accuracy}%")
        print(f"     {'✅ Above random (>55%)' if accuracy > 55 else '⚠️ Marginal (50-55%)' if accuracy > 50 else '❌ At or below random'}")
    else:
        print(f"\n  ⚠️  No directional predictions found (all direction_scores are 0 or null)")


def analyze_triage(outcomes, triages):
    """Section 3: Do Edge 1 triage-boosted signals outperform?"""
    print("\n" + "=" * 70)
    print("SECTION 3: EDGE 1 VALIDATION — DO TRIAGE-BOOSTED SIGNALS OUTPERFORM?")
    print("=" * 70)

    if not triages:
        print("\n  ⚠️  No triage records found. Skipping Edge 1 analysis.")
        return

    # Build entity → triage lookup
    # For each entity, track if any filing was flagged as high materiality
    high_mat_entities = set()
    low_attention_entities = set()
    boosted_entities = set()

    for t in triages:
        eid = t["entity_id"]
        if t["materiality_score"] >= 7:
            high_mat_entities.add(eid)
        if t["attention_likelihood"] == "low":
            low_attention_entities.add(eid)
        if t["signal_boost_applied"]:
            boosted_entities.add(eid)

    print(f"\n  Triage records:           {len(triages)}")
    print(f"  High materiality (≥7):    {len(high_mat_entities)} unique entities")
    print(f"  Low attention:            {len(low_attention_entities)} unique entities")
    print(f"  Boost applied:            {len(boosted_entities)} unique entities")
    print(f"  Quiet filings (high mat + low attention): {len(high_mat_entities & low_attention_entities)}")

    # Triage distribution
    print(f"\n  --- Triage Materiality Distribution ---")
    mat_dist = defaultdict(int)
    for t in triages:
        mat_dist[int(t["materiality_score"])] += 1
    for score in sorted(mat_dist.keys()):
        bar = "█" * mat_dist[score]
        print(f"    Score {score}: {mat_dist[score]:>4}  {bar}")

    # Compare outcomes for high-mat entities vs all others
    high_mat_outcomes = [o for o in outcomes if o["entity_id"] in high_mat_entities]
    other_outcomes = [o for o in outcomes if o["entity_id"] not in high_mat_entities]

    hm_t1d = [o["raw_pct_change_t1d"] for o in high_mat_outcomes
              if o["raw_pct_change_t1d"] is not None]
    ot_t1d = [o["raw_pct_change_t1d"] for o in other_outcomes
              if o["raw_pct_change_t1d"] is not None]

    hm_stats = safe_stats(hm_t1d)
    ot_stats = safe_stats(ot_t1d)

    print(f"\n  --- Outcome Comparison ---")
    print(f"  {'Group':<30} {'Count':>7} {'Mean T+1d':>10} {'Med T+1d':>10} {'Pos %':>8}")
    print(f"  {'-'*28:<30} {'-'*5:>7} {'-'*8:>10} {'-'*8:>10} {'-'*5:>8}")

    for label, s in [("High materiality entities", hm_stats), ("All other entities", ot_stats)]:
        m = f"{s['mean']}%" if s['mean'] is not None else "—"
        md = f"{s['median']}%" if s['median'] is not None else "—"
        p = f"{s['positive_pct']}%" if s['positive_pct'] is not None else "—"
        print(f"  {label:<30} {s['count']:>7} {m:>10} {md:>10} {p:>8}")

    # Quiet filings specifically (high materiality + low attention)
    quiet_entities = high_mat_entities & low_attention_entities
    if quiet_entities:
        quiet_outcomes = [o for o in outcomes if o["entity_id"] in quiet_entities]
        qt_t1d = [o["raw_pct_change_t1d"] for o in quiet_outcomes
                  if o["raw_pct_change_t1d"] is not None]
        qt_stats = safe_stats(qt_t1d)
        m = f"{qt_stats['mean']}%" if qt_stats['mean'] is not None else "—"
        p = f"{qt_stats['positive_pct']}%" if qt_stats['positive_pct'] is not None else "—"
        md = f"{qt_stats['median']}%" if qt_stats['median'] is not None else "—"
        print(f"  {'Quiet filings (HM+LA)':<30} {qt_stats['count']:>7} {m:>10} {md:>10} {p:>8}")

    if hm_stats['mean'] is not None and ot_stats['mean'] is not None:
        diff = round(hm_stats['mean'] - ot_stats['mean'], 4)
        print(f"\n  ⚡ HIGH-MATERIALITY vs OTHERS: {'+' if diff > 0 else ''}{diff}% T+1d difference")
        if abs(diff) < 0.1:
            print(f"     → No meaningful difference. Triage materiality alone doesn't predict returns.")
        else:
            print(f"     → {'Edge 1 triage shows signal' if abs(diff) > 0.3 else 'Small difference, needs more data'}.")

    # Show the actual high-materiality entities and their outcomes
    print(f"\n  --- High-Materiality Entities Detail ---")
    print(f"  {'Entity':<10} {'Mat':>4} {'Attn':<8} {'Direction':<10} {'#Outcomes':>10} {'Mean T+1d':>10}")
    print(f"  {'-'*8:<10} {'-'*3:>4} {'-'*6:<8} {'-'*8:<10} {'-'*8:>10} {'-'*8:>10}")

    for t in sorted(triages, key=lambda x: x["materiality_score"], reverse=True):
        if t["materiality_score"] < 7:
            continue
        eid = t["entity_id"]
        entity_outcomes = [o for o in outcomes if o["entity_id"] == eid]
        entity_t1d = [o["raw_pct_change_t1d"] for o in entity_outcomes
                      if o["raw_pct_change_t1d"] is not None]
        s = safe_stats(entity_t1d)
        m = f"{s['mean']}%" if s['mean'] is not None else "—"
        print(f"  {eid:<10} {int(t['materiality_score']):>4} {t['attention_likelihood']:<8} {t['direction']:<10} {s['count']:>10} {m:>10}")


def analyze_liquidity(outcomes):
    """Section 4: Liquidity profile of signaled entities."""
    print("\n" + "=" * 70)
    print("SECTION 4: LIQUIDITY PROFILE — ARE THESE STOCKS TRADEABLE?")
    print("=" * 70)

    # ADDV distribution
    addvs = [o["addv_20d"] for o in outcomes if o["addv_20d"] and o["addv_20d"] > 0]
    if not addvs:
        print("\n  ⚠️  No ADDV data available. Liquidity analysis skipped.")
        return

    above_min = sum(1 for a in addvs if a >= MIN_ADDV)
    below_min = sum(1 for a in addvs if a < MIN_ADDV)

    print(f"\n  Outcomes with ADDV data:   {len(addvs)}")
    print(f"  ADDV ≥ ${MIN_ADDV:,.0f}:           {above_min} ({round(100*above_min/len(addvs),1)}%)")
    print(f"  ADDV < ${MIN_ADDV:,.0f}:           {below_min} ({round(100*below_min/len(addvs),1)}%)")
    print(f"  Mean ADDV:                 ${mean(addvs):,.0f}")
    print(f"  Median ADDV:               ${median(addvs):,.0f}")

    # Spread analysis
    spreads = [o["spread_at_signal"] for o in outcomes
               if o["spread_at_signal"] and o["spread_at_signal"] > 0
               and o["price_at_signal"] and o["price_at_signal"] > 0]
    if spreads:
        prices = [o["price_at_signal"] for o in outcomes
                  if o["spread_at_signal"] and o["spread_at_signal"] > 0
                  and o["price_at_signal"] and o["price_at_signal"] > 0]
        spread_pcts = [round(100 * s / p, 2) for s, p in zip(spreads, prices) if p > 0]
        if spread_pcts:
            wide_spread = sum(1 for sp in spread_pcts if sp > 2.0)
            print(f"\n  Spread as % of price:")
            print(f"    Mean:         {round(mean(spread_pcts), 2)}%")
            print(f"    Median:       {round(median(spread_pcts), 2)}%")
            print(f"    Wide (>2%):   {wide_spread} ({round(100*wide_spread/len(spread_pcts),1)}%)")

    # Liquid vs illiquid return comparison
    liquid = [o for o in outcomes if o["addv_20d"] and o["addv_20d"] >= MIN_ADDV]
    illiquid = [o for o in outcomes if o["addv_20d"] and o["addv_20d"] < MIN_ADDV and o["addv_20d"] > 0]

    liq_t1d = [o["raw_pct_change_t1d"] for o in liquid if o["raw_pct_change_t1d"] is not None]
    ill_t1d = [o["raw_pct_change_t1d"] for o in illiquid if o["raw_pct_change_t1d"] is not None]

    ls = safe_stats(liq_t1d)
    il = safe_stats(ill_t1d)

    if ls['count'] > 0 and il['count'] > 0:
        print(f"\n  --- Liquid vs Illiquid Returns ---")
        print(f"  {'Group':<25} {'Count':>7} {'Mean T+1d':>10} {'Pos %':>8}")
        print(f"  {'Liquid (ADDV≥$100K)':<25} {ls['count']:>7} {ls['mean']}%{' ':>4} {ls['positive_pct']}%")
        print(f"  {'Illiquid (ADDV<$100K)':<25} {il['count']:>7} {il['mean']}%{' ':>4} {il['positive_pct']}%")


def analyze_entity_concentration(outcomes):
    """Bonus: Are returns dominated by a few entities?"""
    print("\n" + "=" * 70)
    print("SECTION 5: ENTITY CONCENTRATION — WHO DRIVES THE NUMBERS?")
    print("=" * 70)

    entity_counts = defaultdict(int)
    entity_returns = defaultdict(list)
    for o in outcomes:
        eid = o["entity_id"]
        entity_counts[eid] += 1
        if o["raw_pct_change_t1d"] is not None:
            entity_returns[eid].append(o["raw_pct_change_t1d"])

    sorted_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"\n  Unique entities:  {len(entity_counts)}")
    print(f"  Total outcomes:   {len(outcomes)}")

    top_10 = sorted_entities[:10]
    top_10_count = sum(c for _, c in top_10)
    print(f"  Top 10 entities:  {top_10_count} outcomes ({round(100*top_10_count/len(outcomes),1)}%)")

    print(f"\n  --- Top 10 Entities by Outcome Count ---")
    print(f"  {'Entity':<12} {'Outcomes':>9} {'% Total':>8} {'Mean T+1d':>10} {'Med T+1d':>10} {'Pos %':>8}")
    print(f"  {'-'*10:<12} {'-'*7:>9} {'-'*6:>8} {'-'*8:>10} {'-'*8:>10} {'-'*5:>8}")

    for eid, count in top_10:
        pct = round(100 * count / len(outcomes), 1)
        returns = entity_returns.get(eid, [])
        s = safe_stats(returns)
        m = f"{s['mean']}%" if s['mean'] is not None else "—"
        md = f"{s['median']}%" if s['median'] is not None else "—"
        p = f"{s['positive_pct']}%" if s['positive_pct'] is not None else "—"
        print(f"  {eid:<12} {count:>9} {pct:>7}% {m:>10} {md:>10} {p:>8}")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
def write_csvs(outcomes, triages):
    """Write raw data and summary CSVs for manual inspection."""

    # Raw outcome data
    csv_path = "validation_report.csv"
    if outcomes:
        fieldnames = list(outcomes[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(outcomes)
        print(f"\n  📄 Raw data written to: {csv_path} ({len(outcomes)} rows)")

    # Triage data
    if triages:
        triage_path = "validation_triage.csv"
        fieldnames = list(triages[0].keys())
        with open(triage_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(triages)
        print(f"  📄 Triage data written to: {triage_path} ({len(triages)} rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  SignalFFT VALIDATION REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    # Load data
    outcomes = load_outcomes()
    triages = load_triage_records()
    shadows = load_shadow_scores()

    if not outcomes:
        print("\n❌ No outcome data found. Cannot generate report.")
        sys.exit(1)

    # Run analyses
    analyze_baseline(outcomes)
    analyze_direction(outcomes)
    analyze_triage(outcomes, triages)
    analyze_liquidity(outcomes)
    analyze_entity_concentration(outcomes)

    # Write CSVs
    write_csvs(outcomes, triages)

    # Final summary
    print("\n" + "=" * 70)
    print("SUMMARY — WHAT TO LOOK AT")
    print("=" * 70)
    print("""
  1. BASELINE: If the score-bucketed table shows no monotonic relationship
     between signal score and returns, the base scoring model needs work
     before AI edges can improve it.

  2. DIRECTION: If directional accuracy is near 50%, the lexicon + Claude
     direction layer isn't adding value yet. Check if direction_score is
     actually populated (vs all zeros).

  3. EDGE 1 TRIAGE: If high-materiality entities don't outperform, the
     triage is identifying "interesting" filings but not "profitable" ones.
     This is still useful intel — it means the triage needs directional
     refinement, not that it's broken.

  4. LIQUIDITY: If most outcomes are in illiquid stocks (ADDV < $100K),
     the returns may be theoretical, not executable. The risk gateway's
     ADDV floor needs to filter these out.

  5. CONCENTRATION: If 2-3 entities dominate the outcome count, aggregate
     stats are misleading. Per-entity analysis matters more.
    """)


if __name__ == "__main__":
    main()
