"""Quiet Filing Triage service — Edge 1.

Consumes FilingSectionsReady events from the triage-input queue (fan-out
from the section extractor), assesses each filing using Claude, stores
triage results, computes shadow scores, and publishes TriageComplete events.

Fan-out decision: The triage service consumes from a dedicated
``triage-input`` SQS queue. The section extractor fans out the same
FilingSectionsReady event to this queue (same pattern as the
filing-indexer fan-out). This avoids competing with existing
sections-ready consumers and keeps queues single-consumer.

Infrastructure needed (not deployed):
- SQS queue: ``{env}-signalfft-triage-input`` + DLQ
- DynamoDB table: ``{env}-signalfft-shadow-scores`` (PK/SK string keys)
- ANTHROPIC_API_KEY env var (SSM parameter)
- TRIAGE_INPUT_QUEUE_URL env var on ECS task
- SHADOW_SCORES_TABLE env var on ECS task
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
    build_features_pk,
    build_triage_pk,
    build_triage_sk,
    build_shadow_scores_pk,
    build_shadow_scores_sk,
)
from signalfft_common.events import BaseEvent, TriageComplete

from engine.ai_edges.quiet_filing_triage.context_enricher import (
    check_press_release,
    enrich_filing_context,
)
from engine.ai_edges.quiet_filing_triage.triage import (
    TriageAssessment,
    call_triage,
    get_prompt_version,
)

logger = logging.getLogger(__name__)

# Section preference order for triage analysis
_PREFERRED_SECTIONS = [
    "Item7",       # MD&A
    "Item1A",      # Risk Factors
    "Item1",       # Business / 8-K item
    "Item2",       # Properties / 8-K Financial info
    "full_text",   # Fallback
]


class QuietFilingTriageService:
    """SQS consumer that triages filings using Claude."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._s3 = boto3.client("s3", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("TRIAGE_INPUT_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("SECTIONS_READY_QUEUE_URL", "")
        self._events_table = self._dynamo.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )
        self._features_table = self._dynamo.Table(
            os.environ.get("FEATURES_TABLE", f"{self._env}-signalfft-features")
        )
        self._shadow_table = self._dynamo.Table(
            os.environ.get("SHADOW_SCORES_TABLE", f"{self._env}-signalfft-shadow-scores")
        )
        self._bucket = os.environ.get("ARTIFACTS_BUCKET", f"{self._env}-signalfft-artifacts")
        self._boost_multiplier = float(os.environ.get("TRIAGE_BOOST_MULTIPLIER", "1.5"))

    def process_message(self, message: dict) -> bool:
        """Process a single SQS message containing a FilingSectionsReady event."""
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            event_id = payload["event_id"]
            entity_id = payload["entity_id"]
            cik = payload["cik"]
            form_type = payload["form_type"]
            filing_date = payload["filing_date"]
            sections_available = payload["sections_available"]
            section_s3_prefix = payload["section_s3_prefix"]

            # 1. Check cache — skip if already triaged with same prompt version
            prompt_version = get_prompt_version()
            if self._is_cached(entity_id, form_type, filing_date, prompt_version):
                logger.info(
                    "Triage already cached for %s/%s/%s — skipping",
                    entity_id, form_type, filing_date,
                )
                return True

            # 2. Load filing text from S3
            text = self._load_section_text(section_s3_prefix, sections_available)
            if not text:
                logger.warning(
                    "No section text found for %s/%s/%s — skipping triage",
                    entity_id, form_type, filing_date,
                )
                return True

            # 3. Enrich filing context (timing metadata)
            context = enrich_filing_context(filing_date, None, form_type)

            # 4. Check for press release (8-K within +/-1 day)
            has_pr = check_press_release(entity_id, filing_date, self._events_table)
            context["has_press_release"] = has_pr

            # 5. Check Tier 1 keyword matches for this event
            tier1_keywords = self._get_tier1_keywords(event_id)

            # 6. Call Claude triage
            assessment = asyncio.run(call_triage(
                filing_text=text,
                entity_id=entity_id,
                form_type=form_type,
                filing_date=filing_date,
                context=context,
                tier1_keywords=tier1_keywords,
                boost_multiplier=self._boost_multiplier,
            ))

            # 7. Store triage result in DynamoDB
            self._store_triage_result(assessment, event_id)

            # 8. Publish TriageComplete event
            self._emit_triage_complete(assessment, event_id, event.trace_id)

            logger.info(
                "Triage complete: entity=%s form=%s date=%s materiality=%d "
                "attention=%s quiet=%s boost=%.1f",
                entity_id, form_type, filing_date,
                assessment.materiality_score,
                assessment.attention_likelihood,
                assessment.is_quiet_filing,
                assessment.boost_multiplier,
            )
            return True

        except Exception:
            logger.exception(
                "Failed to triage message %s",
                message.get("MessageId", "unknown"),
            )
            return False

    def _load_section_text(
        self,
        section_s3_prefix: str,
        sections_available: list[str],
    ) -> str:
        """Load the best available section text from S3.

        Prefers MD&A, then Risk Factors, then Business, then full_text.
        """
        # Parse s3://bucket/prefix
        prefix = section_s3_prefix.replace("s3://", "")
        parts = prefix.split("/", 1)
        bucket = parts[0]
        key_prefix = parts[1] if len(parts) > 1 else ""

        for section in _PREFERRED_SECTIONS:
            if section in sections_available:
                key = f"{key_prefix}/{section}.txt"
                try:
                    response = self._s3.get_object(Bucket=bucket, Key=key)
                    return response["Body"].read().decode("utf-8")
                except Exception:
                    continue

        # Last resort: try first available section
        if sections_available:
            key = f"{key_prefix}/{sections_available[0]}.txt"
            try:
                response = self._s3.get_object(Bucket=bucket, Key=key)
                return response["Body"].read().decode("utf-8")
            except Exception:
                pass

        return ""

    def _get_tier1_keywords(self, event_id: str) -> list[dict]:
        """Query features table for TRIAGE features for this event."""
        pk = build_features_pk(event_id)
        try:
            response = self._features_table.query(
                KeyConditionExpression="PK = :pk",
                FilterExpression="feature_type = :ft",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":ft": "TRIAGE",
                },
            )
            items = response.get("Items", [])
            if items:
                value = items[0].get("value", {})
                return value.get("matched_terms", [])
        except Exception:
            logger.debug("Could not fetch Tier 1 keywords for event %s", event_id)
        return []

    def _is_cached(
        self,
        entity_id: str,
        form_type: str,
        filing_date: str,
        prompt_version: str,
    ) -> bool:
        """Check if a triage result already exists for this filing + prompt version."""
        pk = build_triage_pk(entity_id)
        sk_prefix = f"TRIAGE#{filing_date}#"

        try:
            response = self._events_table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk_prefix)",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":sk_prefix": sk_prefix,
                },
            )
            for item in response.get("Items", []):
                if item.get("prompt_version") == prompt_version:
                    return True
        except Exception:
            pass
        return False

    def _store_triage_result(
        self,
        assessment: TriageAssessment,
        event_id: str,
    ) -> None:
        """Write triage assessment to DynamoDB events table."""
        pk = build_triage_pk(assessment.entity_id)
        sk = build_triage_sk(assessment.filing_date, event_id)

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "entity_id": assessment.entity_id,
            "event_id": event_id,
            "form_type": assessment.form_type,
            "filing_date": assessment.filing_date,
            "materiality_score": assessment.materiality_score,
            "attention_likelihood": assessment.attention_likelihood,
            "direction": assessment.direction,
            "reasoning": assessment.reasoning,
            "key_material_items": assessment.key_material_items,
            "suggested_urgency": assessment.suggested_urgency,
            "is_quiet_filing": assessment.is_quiet_filing,
            "boost_multiplier": Decimal(str(assessment.boost_multiplier)),
            "claude_model_version": assessment.claude_model_version,
            "prompt_version": assessment.prompt_version,
            "input_tokens": assessment.input_tokens,
            "output_tokens": assessment.output_tokens,
            "estimated_cost": Decimal(str(assessment.estimated_cost)),
            "created_at": assessment.created_at,
            "source": "quiet_filing_triage",
        }

        self._events_table.put_item(Item=item)

    def _emit_triage_complete(
        self,
        assessment: TriageAssessment,
        event_id: str,
        trace_id: str,
    ) -> None:
        """Publish TriageComplete event to the output queue."""
        if not self.output_queue_url:
            return

        now = datetime.now(timezone.utc).isoformat()
        event = TriageComplete(
            timestamp=now,
            source="quiet_filing_triage",
            trace_id=trace_id,
            payload={
                "entity_id": assessment.entity_id,
                "event_id": event_id,
                "materiality_score": assessment.materiality_score,
                "attention_likelihood": assessment.attention_likelihood,
                "direction": assessment.direction,
                "is_quiet_filing": assessment.is_quiet_filing,
                "boost_multiplier": assessment.boost_multiplier,
                "suggested_urgency": assessment.suggested_urgency,
            },
        )
        try:
            self._sqs.send_message(
                QueueUrl=self.output_queue_url,
                MessageBody=event.to_sqs_message(),
            )
        except Exception:
            logger.exception("Failed to emit TriageComplete event")

    def store_shadow_score(
        self,
        entity_id: str,
        signal_id: str,
        original_score: float,
        assessment: TriageAssessment,
    ) -> None:
        """Write shadow score to the shadow_scores DynamoDB table.

        Shadow mode: computes what the boosted score WOULD be without
        modifying the live signal score. Used for validation.
        """
        shadow_score = original_score * assessment.boost_multiplier

        pk = build_shadow_scores_pk(entity_id)
        sk = build_shadow_scores_sk(signal_id, "quiet_filing_triage")

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "entity_id": entity_id,
            "signal_id": signal_id,
            "original_score": Decimal(str(original_score)),
            "shadow_score": Decimal(str(shadow_score)),
            "boost_applied": assessment.is_quiet_filing,
            "boost_multiplier": Decimal(str(assessment.boost_multiplier)),
            "materiality_score": assessment.materiality_score,
            "attention_likelihood": assessment.attention_likelihood,
            "direction": assessment.direction,
            "edge_name": "quiet_filing_triage",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._shadow_table.put_item(Item=item)
