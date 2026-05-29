"""Semantic Delta Analysis service — Edge 2.

Consumes FilingPairReady events from the delta-analysis queue (fan-out
from the filing indexer), compares sequential filings using Claude,
stores per-section results, computes shadow scores, and publishes
DeltaAnalysisComplete events.

Shadow mode only — never modifies live signals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

from signalfft_common.dynamo.keys import (
    build_delta_pk,
    build_delta_sk,
    build_shadow_scores_pk,
    build_shadow_scores_sk,
)
from signalfft_common.events import BaseEvent, DeltaAnalysisComplete

from engine.ai_edges.semantic_delta.analyzer import (
    SemanticShift,
    analyze_delta,
    get_prompt_version,
)
from engine.ai_edges.semantic_delta.scoring import (
    DeltaScore,
    score_delta,
)

logger = logging.getLogger(__name__)

# Target sections by form type
_TARGET_SECTIONS: dict[str, list[str]] = {
    "10-K": ["item_7", "item_1a"],
    "10-K/A": ["item_7", "item_1a"],
    "10-Q": ["part1_item2", "part2_item1a"],
    "10-Q/A": ["part1_item2", "part2_item1a"],
}


class SemanticDeltaService:
    """SQS consumer that performs semantic delta analysis on filing pairs."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._s3 = boto3.client("s3", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("DELTA_ANALYSIS_QUEUE_URL", "")
        self._output_queue_url = os.environ.get("DELTA_COMPLETE_QUEUE_URL", "")

        self._deltas_table = self._dynamo.Table(
            os.environ.get("SEMANTIC_DELTAS_TABLE", f"{self._env}-signalfft-semantic-deltas")
        )
        self._shadow_table = self._dynamo.Table(
            os.environ.get("SHADOW_SCORES_TABLE", f"{self._env}-signalfft-shadow-scores")
        )
        self._bucket = os.environ.get("ARTIFACTS_BUCKET", f"{self._env}-signalfft-artifacts")

    def process_message(self, message: dict) -> bool:
        """Process a single SQS message containing a FilingPairReady event."""
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            entity_id = payload["entity_id"]
            form_type = payload["form_type"]
            current_filing_date = payload["current_filing_date"]
            prior_filing_date = payload["prior_filing_date"]
            current_s3_prefix = payload["current_s3_prefix"]
            prior_s3_prefix = payload["prior_s3_prefix"]
            pair_id = payload["pair_id"]

            # Skip if no prior filing (no comparison possible)
            if prior_s3_prefix is None:
                logger.info(
                    "No prior filing for %s/%s/%s — skipping delta analysis",
                    entity_id, form_type, current_filing_date,
                )
                return True

            # Skip 8-K (no sequential comparison for event-driven filings)
            target_sections = _TARGET_SECTIONS.get(form_type)
            if not target_sections:
                logger.info(
                    "Form type %s not supported for delta analysis — skipping",
                    form_type,
                )
                return True

            # Analyze each target section
            section_results: list[dict] = []
            section_scores: list[DeltaScore] = []

            for section_name in target_sections:
                current_text = self._load_section_text(current_s3_prefix, section_name)
                prior_text = self._load_section_text(prior_s3_prefix, section_name)

                if not current_text or not prior_text:
                    logger.debug(
                        "Missing section %s for %s — skipping",
                        section_name, entity_id,
                    )
                    continue

                # Call Claude analyzer
                shifts = asyncio.run(analyze_delta(
                    current_text=current_text,
                    previous_text=prior_text,
                    entity_id=entity_id,
                    form_type=form_type,
                    section_name=section_name,
                    current_date=current_filing_date,
                    previous_date=prior_filing_date,
                ))

                # Score deterministically
                shift_dicts = [
                    {"shift_type": s.shift_type, "severity": s.severity, "direction": s.direction}
                    for s in shifts
                ]
                delta_score = score_delta(shift_dicts, section_name)

                # Store per-section result
                self._store_section_result(
                    entity_id=entity_id,
                    filing_date=current_filing_date,
                    section_name=section_name,
                    pair_id=pair_id,
                    form_type=form_type,
                    prior_filing_date=prior_filing_date,
                    shifts=shifts,
                    delta_score=delta_score,
                )

                section_results.append({
                    "section_name": section_name,
                    "shift_count": delta_score.shift_count,
                    "composite_score": delta_score.composite_score,
                    "dominant_direction": delta_score.dominant_direction,
                })
                section_scores.append(delta_score)

            # Aggregate across sections
            if section_scores:
                max_score_idx = max(
                    range(len(section_scores)),
                    key=lambda i: section_scores[i].composite_score,
                )
                composite = section_scores[max_score_idx].composite_score
                dominant = section_scores[max_score_idx].dominant_direction
                total_shifts = sum(s.shift_count for s in section_scores)
            else:
                composite = 0.0
                dominant = "neutral"
                total_shifts = 0

            # Write shadow score
            self._store_shadow_score(
                entity_id=entity_id,
                pair_id=pair_id,
                composite_score=composite,
                dominant_direction=dominant,
                shift_count=total_shifts,
                sections_analyzed=[r["section_name"] for r in section_results],
            )

            # Emit DeltaAnalysisComplete
            self._emit_delta_complete(
                entity_id=entity_id,
                pair_id=pair_id,
                current_filing_date=current_filing_date,
                prior_filing_date=prior_filing_date,
                form_type=form_type,
                sections_analyzed=[r["section_name"] for r in section_results],
                shift_count=total_shifts,
                composite_score=composite,
                dominant_direction=dominant,
                trace_id=event.trace_id,
            )

            logger.info(
                "Delta analysis complete: entity=%s form=%s date=%s "
                "sections=%d shifts=%d composite=%.4f direction=%s",
                entity_id, form_type, current_filing_date,
                len(section_results), total_shifts, composite, dominant,
            )
            return True

        except Exception:
            logger.exception(
                "Failed to process delta analysis message %s",
                message.get("MessageId", "unknown"),
            )
            return False

    def _load_section_text(self, s3_prefix: str, section_name: str) -> str:
        """Load a section's text from S3."""
        prefix = s3_prefix.replace("s3://", "")
        parts = prefix.split("/", 1)
        bucket = parts[0]
        key_prefix = parts[1] if len(parts) > 1 else ""

        key = f"{key_prefix}/{section_name}.txt"
        try:
            response = self._s3.get_object(Bucket=bucket, Key=key)
            return response["Body"].read().decode("utf-8")
        except Exception:
            return ""

    def _store_section_result(
        self,
        entity_id: str,
        filing_date: str,
        section_name: str,
        pair_id: str,
        form_type: str,
        prior_filing_date: str,
        shifts: list[SemanticShift],
        delta_score: DeltaScore,
    ) -> None:
        """Write per-section delta result to semantic_deltas table."""
        pk = build_delta_pk(entity_id)
        sk = build_delta_sk(filing_date, section_name)

        shifts_data = [
            {
                "shift_type": s.shift_type,
                "description": s.description,
                "severity": s.severity,
                "direction": s.direction,
                "evidence": s.evidence,
            }
            for s in shifts
        ]

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "entity_id": entity_id,
            "filing_date": filing_date,
            "section_name": section_name,
            "pair_id": pair_id,
            "form_type": form_type,
            "prior_filing_date": prior_filing_date,
            "shifts": shifts_data,
            "shift_count": delta_score.shift_count,
            "composite_score": Decimal(str(delta_score.composite_score)),
            "dominant_direction": delta_score.dominant_direction,
            "top_shift_type": delta_score.top_shift_type,
            "prompt_version": get_prompt_version(),
            "source": "semantic_delta",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._deltas_table.put_item(Item=item)

    def _store_shadow_score(
        self,
        entity_id: str,
        pair_id: str,
        composite_score: float,
        dominant_direction: str,
        shift_count: int,
        sections_analyzed: list[str],
    ) -> None:
        """Write aggregate shadow score to shadow_scores table."""
        pk = build_shadow_scores_pk(entity_id)
        sk = build_shadow_scores_sk(pair_id, "semantic_delta")

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "entity_id": entity_id,
            "pair_id": pair_id,
            "composite_score": Decimal(str(composite_score)),
            "dominant_direction": dominant_direction,
            "shift_count": shift_count,
            "sections_analyzed": sections_analyzed,
            "edge_name": "semantic_delta",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._shadow_table.put_item(Item=item)

    def _emit_delta_complete(
        self,
        entity_id: str,
        pair_id: str,
        current_filing_date: str,
        prior_filing_date: str,
        form_type: str,
        sections_analyzed: list[str],
        shift_count: int,
        composite_score: float,
        dominant_direction: str,
        trace_id: str,
    ) -> None:
        """Publish DeltaAnalysisComplete event to the output queue."""
        if not self._output_queue_url:
            return

        now = datetime.now(timezone.utc).isoformat()
        event = DeltaAnalysisComplete(
            timestamp=now,
            source="semantic_delta",
            trace_id=trace_id,
            payload={
                "entity_id": entity_id,
                "pair_id": pair_id,
                "current_filing_date": current_filing_date,
                "prior_filing_date": prior_filing_date,
                "form_type": form_type,
                "sections_analyzed": sections_analyzed,
                "shift_count": shift_count,
                "composite_score": composite_score,
                "dominant_direction": dominant_direction,
            },
        )
        try:
            self._sqs.send_message(
                QueueUrl=self._output_queue_url,
                MessageBody=event.to_sqs_message(),
            )
        except Exception:
            logger.exception("Failed to emit DeltaAnalysisComplete event")
