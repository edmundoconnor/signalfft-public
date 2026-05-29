"""Dashboard API ECS Fargate service.

HTTP server providing pipeline status, signal intelligence, and health endpoints
for the SignalFFT web dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse, parse_qs

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

CACHE_TTL = 15  # seconds
EXTENDED_CACHE_TTL = 60  # seconds, for expensive aggregate counts


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard API."""

    _dynamo = None
    _sqs = None
    _tables: dict[str, str] = {}
    _queues: dict[str, str] = {}
    _cache: dict[str, Any] = {}
    _cache_ts: dict[str, float] = {}

    @classmethod
    def init_aws(cls) -> None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        env = os.environ.get("ENVIRONMENT", "dev")
        cls._dynamo = boto3.client("dynamodb", region_name=region)
        cls._sqs = boto3.client("sqs", region_name=region)
        cls._tables = {
            "entities": os.environ.get("ENTITIES_TABLE", f"{env}-signalfft-entities"),
            "events": os.environ.get("EVENTS_TABLE", f"{env}-signalfft-events"),
            "features": os.environ.get("FEATURES_TABLE", f"{env}-signalfft-features"),
            "signals": os.environ.get("SIGNALS_TABLE", f"{env}-signalfft-signals"),
            "waves": os.environ.get("WAVES_TABLE", f"{env}-signalfft-waves"),
            "narratives": os.environ.get("NARRATIVES_TABLE", f"{env}-signalfft-narratives"),
            "attention_field": os.environ.get("ATTENTION_FIELD_TABLE", f"{env}-signalfft-attention-field"),
            "trade_candidates": os.environ.get("TRADE_CANDIDATES_TABLE", f"{env}-signalfft-trade-candidates"),
            "outcomes": os.environ.get("OUTCOMES_TABLE", f"{env}-signalfft-outcomes"),
            "shadow_scores": os.environ.get("SHADOW_SCORES_TABLE", f"{env}-signalfft-shadow-scores"),
            "semantic_deltas": os.environ.get("SEMANTIC_DELTAS_TABLE", f"{env}-signalfft-semantic-deltas"),
        }
        cls._queues = {
            "raw_events": os.environ.get("RAW_EVENTS_QUEUE_URL", ""),
            "features": os.environ.get("FEATURES_QUEUE_URL", ""),
            "signals": os.environ.get("SIGNALS_QUEUE_URL", ""),
            "candidates": os.environ.get("CANDIDATES_QUEUE_URL", ""),
            "waves": os.environ.get("WAVES_QUEUE_URL", ""),
            "execution": os.environ.get("EXECUTION_QUEUE_URL", ""),
        }

    # ----- caching helpers -----

    @classmethod
    def _get_cached(cls, key: str, ttl: int = CACHE_TTL) -> Any | None:
        ts = cls._cache_ts.get(key)
        if ts is not None and (time.time() - ts) < ttl:
            return cls._cache.get(key)
        return None

    @classmethod
    def _set_cached(cls, key: str, value: Any) -> None:
        cls._cache[key] = value
        cls._cache_ts[key] = time.time()

    # ----- DynamoDB helpers -----

    def _scan_all(self, table_name: str) -> list[dict]:
        """Scan a DynamoDB table, paginating through all items."""
        items: list[dict] = []
        kwargs: dict[str, Any] = {"TableName": table_name}
        while True:
            resp = self._dynamo.scan(**kwargs)
            for raw in resp.get("Items", []):
                items.append(_unmarshall_dynamo_item(raw))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def _scan_with_filter(
        self,
        table_name: str,
        filter_expr: str,
        expr_values: dict,
    ) -> list[dict]:
        """Scan a DynamoDB table with a filter expression, paginating."""
        items: list[dict] = []
        kwargs: dict[str, Any] = {
            "TableName": table_name,
            "FilterExpression": filter_expr,
            "ExpressionAttributeValues": expr_values,
        }
        while True:
            resp = self._dynamo.scan(**kwargs)
            for raw in resp.get("Items", []):
                items.append(_unmarshall_dynamo_item(raw))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def _safe_table_count(self, table_name: str) -> int:
        """Get count of items in a table, returning 0 on error."""
        try:
            count = 0
            kwargs: dict[str, Any] = {"TableName": table_name, "Select": "COUNT"}
            while True:
                resp = self._dynamo.scan(**kwargs)
                count += resp.get("Count", 0)
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            return count
        except Exception:
            return 0

    # ----- routing -----

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            "/health": lambda: self._respond(200, {"status": "healthy"}),
            "/api/pipeline/status": self._handle_pipeline_status,
            "/api/pipeline/flow": self._handle_pipeline_flow,
            "/api/signals/recent": lambda: self._handle_signals_recent(params),
            "/api/waves/active": lambda: self._handle_recent_items("waves", params),
            "/api/narratives/active": lambda: self._handle_recent_items("narratives", params),
            "/api/candidates/recent": lambda: self._handle_recent_items("trade_candidates", params),
            "/api/attention/current": self._handle_attention_current,
            "/api/entities/top": lambda: self._handle_recent_items("entities", params),
            "/api/queues/status": self._handle_queue_status,
            "/api/metrics/summary": self._handle_metrics_summary,
            "/api/triage/recent": self._handle_triage_recent,
            "/api/outcomes/recent": self._handle_outcomes_recent,
            "/api/deltas/recent": self._handle_deltas_recent,
            "/api/shadows/comparison": self._handle_shadows_comparison,
            "/api/filing-pipeline/status": self._handle_filing_pipeline_status,
            "/api/metrics/extended": self._handle_metrics_extended,
        }

        handler = routes.get(path)
        if handler:
            handler()
        elif path.startswith("/api/"):
            self._respond(404, {"error": "not found"})
        else:
            self._respond(200, {"service": "signalfft-dashboard"})

    # ----- existing handlers -----

    def _handle_pipeline_status(self) -> None:
        try:
            counts: dict[str, int] = {}
            for name, table_name in self._tables.items():
                try:
                    resp = self._dynamo.scan(TableName=table_name, Select="COUNT")
                    counts[name] = resp.get("Count", 0)
                except Exception:
                    counts[name] = -1
            self._respond(200, {"pipeline": counts, "status": "running"})
        except Exception as e:
            logger.exception("Error getting pipeline status")
            self._respond(500, {"error": str(e)})

    def _handle_pipeline_flow(self) -> None:
        """Return pipeline stage metadata for the flow visualization."""
        try:
            counts: dict[str, int] = {}
            for name, table_name in self._tables.items():
                try:
                    resp = self._dynamo.scan(TableName=table_name, Select="COUNT")
                    counts[name] = resp.get("Count", 0)
                except Exception:
                    counts[name] = -1

            queue_depths = self._get_queue_depths()

            stages = [
                {
                    "id": "collectors",
                    "label": "Collectors",
                    "sublabel": "SEC / News / Social",
                    "count": counts.get("events", 0),
                    "queue_depth": queue_depths.get("raw_events", 0),
                    "status": "healthy" if counts.get("events", -1) >= 0 else "error",
                },
                {
                    "id": "features",
                    "label": "Feature Extraction",
                    "sublabel": "Mentions / Sentiment / Temporal",
                    "count": counts.get("features", 0),
                    "queue_depth": queue_depths.get("features", 0),
                    "status": "healthy" if counts.get("features", -1) >= 0 else "error",
                },
                {
                    "id": "signals",
                    "label": "Signal Scoring",
                    "sublabel": "7-Component Model",
                    "count": counts.get("signals", 0),
                    "queue_depth": queue_depths.get("signals", 0),
                    "status": "healthy" if counts.get("signals", -1) >= 0 else "error",
                },
                {
                    "id": "waves",
                    "label": "Wave Detection",
                    "sublabel": "Density Bursts",
                    "count": counts.get("waves", 0),
                    "queue_depth": queue_depths.get("waves", 0),
                    "status": "healthy" if counts.get("waves", -1) >= 0 else "error",
                },
                {
                    "id": "narratives",
                    "label": "Narrative Gravity",
                    "sublabel": "Story Arc Tracking",
                    "count": counts.get("narratives", 0),
                    "queue_depth": 0,
                    "status": "healthy" if counts.get("narratives", -1) >= 0 else "error",
                },
                {
                    "id": "risk_gate",
                    "label": "Risk Gateway",
                    "sublabel": "Deterministic Rules",
                    "count": counts.get("trade_candidates", 0),
                    "queue_depth": queue_depths.get("candidates", 0),
                    "status": "healthy" if counts.get("trade_candidates", -1) >= 0 else "error",
                },
            ]

            self._respond(200, {
                "stages": stages,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.exception("Error getting pipeline flow")
            self._respond(500, {"error": str(e)})

    def _handle_recent_items(self, table_key: str, params: dict) -> None:
        """Scan a DynamoDB table for recent items (limited)."""
        try:
            table_name = self._tables.get(table_key, "")
            limit = int(params.get("limit", ["20"])[0])
            limit = min(limit, 100)

            resp = self._dynamo.scan(
                TableName=table_name,
                Limit=limit,
            )

            items = []
            for raw_item in resp.get("Items", []):
                item = _unmarshall_dynamo_item(raw_item)
                items.append(item)

            self._respond(200, {
                "items": items,
                "count": len(items),
                "table": table_key,
            })
        except Exception as e:
            logger.exception("Error scanning %s", table_key)
            self._respond(500, {"error": str(e)})

    def _handle_attention_current(self) -> None:
        """Return the most recent attention field snapshot."""
        try:
            table_name = self._tables.get("attention_field", "")
            resp = self._dynamo.scan(
                TableName=table_name,
                Limit=1,
            )
            items = resp.get("Items", [])
            if items:
                item = _unmarshall_dynamo_item(items[0])
                self._respond(200, {"attention_field": item})
            else:
                self._respond(200, {"attention_field": None})
        except Exception as e:
            logger.exception("Error getting attention field")
            self._respond(500, {"error": str(e)})

    def _handle_queue_status(self) -> None:
        """Return SQS queue depths and DLQ status."""
        try:
            queue_info = {}
            for name, url in self._queues.items():
                if not url:
                    queue_info[name] = {"depth": -1, "status": "unconfigured"}
                    continue
                try:
                    resp = self._sqs.get_queue_attributes(
                        QueueUrl=url,
                        AttributeNames=[
                            "ApproximateNumberOfMessages",
                            "ApproximateNumberOfMessagesNotVisible",
                            "ApproximateNumberOfMessagesDelayed",
                        ],
                    )
                    attrs = resp.get("Attributes", {})
                    depth = int(attrs.get("ApproximateNumberOfMessages", 0))
                    in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
                    delayed = int(attrs.get("ApproximateNumberOfMessagesDelayed", 0))
                    queue_info[name] = {
                        "depth": depth,
                        "in_flight": in_flight,
                        "delayed": delayed,
                        "status": "healthy" if depth < 1000 else "backlogged",
                    }
                except Exception:
                    queue_info[name] = {"depth": -1, "status": "error"}

            self._respond(200, {"queues": queue_info})
        except Exception as e:
            logger.exception("Error getting queue status")
            self._respond(500, {"error": str(e)})

    def _handle_metrics_summary(self) -> None:
        """Return aggregated metrics for the dashboard summary strip."""
        try:
            counts: dict[str, int] = {}
            for name, table_name in self._tables.items():
                try:
                    resp = self._dynamo.scan(TableName=table_name, Select="COUNT")
                    counts[name] = resp.get("Count", 0)
                except Exception:
                    counts[name] = 0

            queue_depths = self._get_queue_depths()
            total_queue = sum(d for d in queue_depths.values() if d >= 0)

            self._respond(200, {
                "metrics": {
                    "total_events": counts.get("events", 0),
                    "total_features": counts.get("features", 0),
                    "total_signals": counts.get("signals", 0),
                    "active_waves": counts.get("waves", 0),
                    "active_narratives": counts.get("narratives", 0),
                    "trade_candidates": counts.get("trade_candidates", 0),
                    "tracked_entities": counts.get("entities", 0),
                    "total_queue_depth": total_queue,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.exception("Error getting metrics summary")
            self._respond(500, {"error": str(e)})

    # ----- new / modified endpoint handlers -----

    def _handle_signals_recent(self, params: dict) -> None:
        """Scan signals table with direction score and label."""
        cache_key = "signals_recent"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("signals", "")
            limit = int(params.get("limit", ["20"])[0])
            limit = min(limit, 100)

            resp = self._dynamo.scan(TableName=table_name, Limit=limit)
            items = []
            for raw_item in resp.get("Items", []):
                item = _unmarshall_dynamo_item(raw_item)
                ds = item.get("direction_score")
                if ds is None:
                    ds = 0.0
                item["direction_score"] = ds
                if ds > 0.1:
                    item["direction_label"] = "LONG"
                elif ds < -0.1:
                    item["direction_label"] = "SHORT"
                else:
                    item["direction_label"] = "NEUTRAL"
                items.append(item)

            body = {"items": items, "count": len(items), "table": "signals"}
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except Exception as e:
            logger.exception("Error scanning signals")
            self._respond(500, {"error": str(e)})

    def _handle_triage_recent(self) -> None:
        """Return recent triage assessments from events table."""
        cache_key = "triage_recent"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("events", "")
            items = self._scan_with_filter(
                table_name,
                "begins_with(SK, :prefix)",
                {":prefix": {"S": "TRIAGE#"}},
            )
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            total_count = len(items)
            high_mat = sum(
                1 for i in items if (i.get("materiality_score") or 0) >= 7
            )
            recent = items[:20]

            body = {
                "triages": [
                    {
                        "entity_id": i.get("entity_id"),
                        "filing_date": i.get("filing_date"),
                        "form_type": i.get("form_type"),
                        "materiality_score": i.get("materiality_score"),
                        "attention_likelihood": i.get("attention_likelihood"),
                        "direction": i.get("direction"),
                        "suggested_urgency": i.get("suggested_urgency"),
                        "reasoning": i.get("reasoning"),
                        "key_material_items": i.get("key_material_items", []),
                        "is_after_hours": i.get("is_after_hours"),
                        "is_friday": i.get("is_friday"),
                        "signal_boost_applied": i.get("is_quiet_filing", False),
                        "created_at": i.get("created_at"),
                    }
                    for i in recent
                ],
                "total_count": total_count,
                "high_materiality_count": high_mat,
            }
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except Exception as e:
            logger.exception("Error getting triage data")
            self._respond(500, {"error": str(e)})

    def _handle_outcomes_recent(self) -> None:
        """Return recent outcomes with price data and summary stats."""
        cache_key = "outcomes_recent"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("outcomes", "")
            all_items = self._scan_all(table_name)

            with_t1d = [i for i in all_items if i.get("price_t1d") is not None]
            with_t1d.sort(
                key=lambda x: x.get("signal_timestamp", ""), reverse=True
            )
            recent = with_t1d[:30]

            # Summary over ALL outcomes with T+1d data
            pct_changes = [
                i["raw_pct_change_t1d"]
                for i in with_t1d
                if i.get("raw_pct_change_t1d") is not None
            ]
            avg_pct = (
                sum(pct_changes) / len(pct_changes) if pct_changes else 0.0
            )
            positive = sum(1 for p in pct_changes if p > 0)
            positive_pct = (
                (positive / len(pct_changes) * 100) if pct_changes else 0.0
            )

            body = {
                "outcomes": [
                    {
                        "entity_id": i.get("entity_id"),
                        "ticker": i.get("ticker"),
                        "signal_score": i.get("signal_score"),
                        "direction_score": i.get("direction_score"),
                        "price_at_signal": i.get("price_at_signal"),
                        "price_t1d": i.get("price_t1d"),
                        "price_t5d": i.get("price_t5d"),
                        "raw_pct_change_t1d": i.get("raw_pct_change_t1d"),
                        "spread_adj_pct_change_t1d": i.get(
                            "spread_adj_pct_change_t1d"
                        ),
                        "raw_pct_change_t5d": i.get("raw_pct_change_t5d"),
                        "spread_at_signal": i.get("spread_at_signal"),
                        "addv_20d": i.get("addv_20d"),
                        "signal_timestamp": i.get("signal_timestamp"),
                    }
                    for i in recent
                ],
                "summary": {
                    "total_with_t1d": len(with_t1d),
                    "avg_pct_change_t1d": round(avg_pct, 2),
                    "positive_t1d_pct": round(positive_pct, 1),
                },
            }
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                self._respond(200, {
                    "outcomes": [],
                    "summary": {
                        "total_with_t1d": 0,
                        "avg_pct_change_t1d": 0.0,
                        "positive_t1d_pct": 0.0,
                    },
                })
            else:
                logger.exception("Error getting outcomes data")
                self._respond(500, {"error": str(exc)})
        except Exception as e:
            logger.exception("Error getting outcomes data")
            self._respond(500, {"error": str(e)})

    def _handle_deltas_recent(self) -> None:
        """Return recent semantic delta analyses."""
        cache_key = "deltas_recent"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("semantic_deltas", "")
            all_items = self._scan_all(table_name)

            all_items.sort(
                key=lambda x: x.get("created_at", ""), reverse=True
            )
            total_count = len(all_items)
            recent = all_items[:20]

            body = {
                "deltas": [
                    {
                        "entity_id": i.get("entity_id"),
                        "form_type": i.get("form_type"),
                        "section_name": i.get("section_name"),
                        "current_filing_date": i.get("filing_date"),
                        "previous_filing_date": i.get("prior_filing_date"),
                        "shift_count": i.get("shift_count", 0),
                        "max_severity": max(
                            (s.get("severity", 0) for s in (i.get("shifts") or [])),
                            default=0,
                        ),
                        "overall_tone_change": i.get("dominant_direction"),
                        "direction_consensus": i.get("dominant_direction"),
                        "mapped_semantic_impact": i.get("composite_score", 0),
                        "reasoning": i.get("top_shift_type"),
                        "shifts": i.get("shifts", []),
                        "created_at": i.get("created_at"),
                    }
                    for i in recent
                ],
                "total_count": total_count,
            }
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("ResourceNotFoundException", "AccessDeniedException"):
                self._respond(200, {
                    "deltas": [],
                    "total_count": 0,
                    "status": "table_not_provisioned",
                })
            else:
                logger.exception("Error getting deltas data")
                self._respond(500, {"error": str(exc)})
        except Exception as e:
            logger.exception("Error getting deltas data")
            self._respond(500, {"error": str(e)})

    def _handle_shadows_comparison(self) -> None:
        """Return shadow score comparisons, grouping edge1 and edge2 per signal."""
        cache_key = "shadows_comparison"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("shadow_scores", "")
            all_items = self._scan_all(table_name)
            all_items.sort(
                key=lambda x: x.get("created_at", ""), reverse=True
            )
            recent = all_items[:30]

            grouped: dict[str, dict] = {}
            for item in recent:
                edge_name = item.get("edge_name", "")
                sig_id = item.get("signal_id") or item.get("pair_id", "")

                if sig_id not in grouped:
                    grouped[sig_id] = {
                        "entity_id": item.get("entity_id"),
                        "signal_id": sig_id,
                        "original_score": None,
                        "triage_boost": 0.0,
                        "delta_impact": 0.0,
                        "shadow_semantic_impact": 0.0,
                        "score_delta": 0.0,
                        "direction_consensus": None,
                        "edges_contributing": [],
                        "created_at": item.get("created_at"),
                    }

                entry = grouped[sig_id]
                if edge_name not in entry["edges_contributing"]:
                    entry["edges_contributing"].append(edge_name)

                if edge_name == "quiet_filing_triage":
                    orig = item.get("original_score") or 0
                    shadow = item.get("shadow_score") or 0
                    entry["triage_boost"] = round(shadow - orig, 4)
                    entry["original_score"] = item.get("original_score")
                    entry["direction_consensus"] = item.get("direction")
                elif edge_name == "semantic_delta":
                    entry["delta_impact"] = item.get("composite_score", 0)
                    entry["shadow_semantic_impact"] = item.get(
                        "composite_score", 0
                    )
                    if item.get("dominant_direction"):
                        entry["direction_consensus"] = item.get(
                            "dominant_direction"
                        )

                entry["score_delta"] = round(
                    (entry.get("triage_boost") or 0)
                    + (entry.get("delta_impact") or 0),
                    4,
                )

            body = {"comparisons": list(grouped.values())}
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                self._respond(200, {"comparisons": []})
            else:
                logger.exception("Error getting shadow scores")
                self._respond(500, {"error": str(exc)})
        except Exception as e:
            logger.exception("Error getting shadow scores")
            self._respond(500, {"error": str(e)})

    def _handle_filing_pipeline_status(self) -> None:
        """Return filing pair index status from events table."""
        cache_key = "filing_pipeline_status"
        cached = self._get_cached(cache_key)
        if cached:
            self._respond(200, cached)
            return
        try:
            table_name = self._tables.get("events", "")
            items = self._scan_with_filter(
                table_name,
                "begins_with(SK, :prefix)",
                {":prefix": {"S": "PAIR#"}},
            )

            total = len(items)
            by_form_type: dict[str, int] = {}
            for i in items:
                ft = i.get("form_type", "unknown")
                by_form_type[ft] = by_form_type.get(ft, 0) + 1

            items.sort(
                key=lambda x: x.get("created_at", ""), reverse=True
            )
            recent: list[dict[str, Any]] = []
            for i in items[:5]:
                pair_info: dict[str, Any] = {
                    "entity_id": i.get("entity_id"),
                    "form_type": i.get("form_type"),
                    "current_date": i.get("current_filing_date"),
                    "previous_date": i.get("prior_filing_date"),
                }
                if i.get("prior_s3_prefix") is None:
                    pair_info["sections_pending"] = True
                recent.append(pair_info)

            body = {
                "filing_pairs": {
                    "total": total,
                    "by_form_type": by_form_type,
                    "recent": recent,
                }
            }
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except Exception as e:
            logger.exception("Error getting filing pipeline status")
            self._respond(500, {"error": str(e)})

    def _handle_metrics_extended(self) -> None:
        """Return extended metrics with cached counts (60s TTL)."""
        cache_key = "metrics_extended"
        cached = self._get_cached(cache_key, EXTENDED_CACHE_TTL)
        if cached:
            self._respond(200, cached)
            return
        try:
            outcomes_count = self._safe_table_count(
                self._tables.get("outcomes", "")
            )
            shadow_count = self._safe_table_count(
                self._tables.get("shadow_scores", "")
            )
            deltas_count = self._safe_table_count(
                self._tables.get("semantic_deltas", "")
            )

            events_table = self._tables.get("events", "")
            pairs = self._scan_with_filter(
                events_table,
                "begins_with(SK, :prefix)",
                {":prefix": {"S": "PAIR#"}},
            )
            triages = self._scan_with_filter(
                events_table,
                "begins_with(SK, :prefix)",
                {":prefix": {"S": "TRIAGE#"}},
            )
            high_mat = sum(
                1 for i in triages if (i.get("materiality_score") or 0) >= 7
            )

            body = {
                "outcomes_tracked": outcomes_count,
                "filing_pairs": len(pairs),
                "shadow_scores": shadow_count,
                "triage_high_materiality": high_mat,
                "deltas_detected": deltas_count,
            }
            self._set_cached(cache_key, body)
            self._respond(200, body)
        except Exception as e:
            logger.exception("Error getting extended metrics")
            self._respond(500, {"error": str(e)})

    # ----- response & utility -----

    def _get_queue_depths(self) -> dict[str, int]:
        """Retrieve approximate message count for each configured queue."""
        depths: dict[str, int] = {}
        for name, url in self._queues.items():
            if not url:
                depths[name] = -1
                continue
            try:
                resp = self._sqs.get_queue_attributes(
                    QueueUrl=url,
                    AttributeNames=["ApproximateNumberOfMessages"],
                )
                depths[name] = int(
                    resp.get("Attributes", {}).get("ApproximateNumberOfMessages", 0)
                )
            except Exception:
                depths[name] = -1
        return depths

    def _respond(self, status: int, body: Any) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(format, *args)


def _unmarshall_dynamo_item(raw: dict) -> dict:
    """Convert DynamoDB-marshalled item to plain dict."""
    result = {}
    for key, value in raw.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = float(value["N"])
        elif "BOOL" in value:
            result[key] = value["BOOL"]
        elif "M" in value:
            result[key] = _unmarshall_dynamo_item(value["M"])
        elif "L" in value:
            result[key] = [_unmarshall_dynamo_item({"_": v}).get("_", v) for v in value["L"]]
        elif "SS" in value:
            result[key] = list(value["SS"])
        elif "NS" in value:
            result[key] = [float(n) for n in value["NS"]]
        elif "NULL" in value:
            result[key] = None
        else:
            result[key] = str(value)
    return result


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    DashboardHandler.init_aws()
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    logger.info("Dashboard API starting on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
