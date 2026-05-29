"""Filing pair and chain indexer service (F1.3 / F1.4 / F1.5).

Consumes ``FilingSectionsReady`` events from the filing-indexer queue,
backfills filing history from the SEC Submissions API, builds pair and
chain indexes in DynamoDB, and emits downstream events.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

from signalfft_common.dynamo.keys import (
    build_filing_chains_pk,
    build_filing_chains_sk,
    build_filing_pairs_pk,
    build_filing_pairs_sk,
    build_filing_sections_pk,
    build_filing_sections_sk,
)
from signalfft_common.events import BaseEvent, FilingChainReady, FilingPairReady

from engine.filing_processing.backfill import fetch_filing_history

logger = logging.getLogger(__name__)


class FilingIndexerService:
    """Consumes FilingSectionsReady events, builds pair + chain indexes."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("FILING_INDEXER_QUEUE_URL", "")
        self._output_queue_url = os.environ.get("FILING_INDEX_READY_QUEUE_URL", "")
        self._delta_analysis_queue_url = os.environ.get("DELTA_ANALYSIS_QUEUE_URL", "")
        self._events_table = self._dynamo.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )
        self._user_agent = os.environ.get("EDGAR_USER_AGENT", "SignalFFT/0.1")

    # ------------------------------------------------------------------
    # Public interface (called by runner)
    # ------------------------------------------------------------------

    def process_message(self, message: dict) -> None:
        """Process a single SQS message containing a FilingSectionsReady event."""
        event = BaseEvent.from_sqs_message(message["Body"])
        payload = event.payload

        entity_id = payload["entity_id"]
        cik = payload["cik"]
        form_type = payload["form_type"]
        filing_date = payload["filing_date"]
        section_s3_prefix = payload["section_s3_prefix"]

        # 1. Backfill: fetch filing history from SEC
        history = fetch_filing_history(cik, form_type, self._user_agent)

        # 2. Build pair: find the prior filing of same form_type
        pair_record = self._build_filing_pair(
            entity_id, form_type, filing_date, section_s3_prefix, history,
        )

        # 3. Build/update chain
        chain_record = self._build_filing_chain(
            entity_id, form_type, filing_date, history,
        )

        # 4. Emit events
        if pair_record:
            self._emit_pair_ready(pair_record)
            self._emit_pair_ready_to_delta(pair_record)
        self._emit_chain_ready(chain_record)

        logger.info(
            "Indexed filing %s/%s/%s: pair=%s, chain_length=%d",
            entity_id,
            form_type,
            filing_date,
            "yes" if pair_record else "no-prior",
            chain_record["chain_length"],
        )

    # ------------------------------------------------------------------
    # F1.4 — Pair indexing
    # ------------------------------------------------------------------

    def _build_filing_pair(
        self,
        entity_id: str,
        form_type: str,
        filing_date: str,
        section_s3_prefix: str,
        history: list[dict],
    ) -> dict | None:
        """Build a pair record linking current filing to its predecessor.

        Returns the pair record dict (always written to DynamoDB).
        Returns ``None`` only when there is no filing history at all.
        """
        # Find prior filing
        prior_filing_date: str | None = None
        prior_s3_prefix: str | None = None

        sorted_history = sorted(history, key=lambda h: h["filing_date"])
        prior_dates = [
            h["filing_date"] for h in sorted_history if h["filing_date"] < filing_date
        ]

        if prior_dates:
            prior_filing_date = prior_dates[-1]
            prior_s3_prefix = self._lookup_section_s3_prefix(
                entity_id, form_type, prior_filing_date,
            )

        now = datetime.now(timezone.utc).isoformat()
        pair_id = str(uuid.uuid4())

        pair_record = {
            "PK": build_filing_pairs_pk(entity_id),
            "SK": build_filing_pairs_sk(form_type, filing_date),
            "entity_id": entity_id,
            "form_type": form_type,
            "current_filing_date": filing_date,
            "prior_filing_date": prior_filing_date,
            "current_s3_prefix": section_s3_prefix,
            "prior_s3_prefix": prior_s3_prefix,
            "pair_id": pair_id,
            "created_at": now,
        }

        self._events_table.put_item(Item=pair_record)
        return pair_record

    def _lookup_section_s3_prefix(
        self,
        entity_id: str,
        form_type: str,
        filing_date: str,
    ) -> str | None:
        """Look up the S3 prefix for a prior filing's extracted sections."""
        try:
            response = self._events_table.get_item(
                Key={
                    "PK": build_filing_sections_pk(entity_id),
                    "SK": build_filing_sections_sk(form_type, filing_date),
                },
            )
            item = response.get("Item")
            if item:
                return item.get("section_s3_prefix")
        except Exception:
            logger.warning(
                "Failed to look up sections for %s/%s/%s",
                entity_id, form_type, filing_date,
                exc_info=True,
            )
        return None

    # ------------------------------------------------------------------
    # F1.5 — Chain indexing
    # ------------------------------------------------------------------

    def _build_filing_chain(
        self,
        entity_id: str,
        form_type: str,
        filing_date: str,
        history: list[dict],
    ) -> dict:
        """Build or update the chain record for this entity/form_type."""
        sorted_dates = sorted({h["filing_date"] for h in history})

        # Ensure the current filing_date is in the chain
        if filing_date not in sorted_dates:
            sorted_dates.append(filing_date)
            sorted_dates.sort()

        now = datetime.now(timezone.utc).isoformat()
        chain_id = str(uuid.uuid4())

        pk = build_filing_chains_pk(entity_id)
        sk = build_filing_chains_sk(form_type)

        # Try to preserve existing chain_id on update
        try:
            existing = self._events_table.get_item(Key={"PK": pk, "SK": sk})
            if existing.get("Item"):
                chain_id = existing["Item"].get("chain_id", chain_id)
        except Exception:
            pass  # First chain creation — use new UUID

        chain_record = {
            "PK": pk,
            "SK": sk,
            "entity_id": entity_id,
            "form_type": form_type,
            "chain_length": len(sorted_dates),
            "latest_filing_date": sorted_dates[-1],
            "filing_dates": sorted_dates,
            "chain_id": chain_id,
            "updated_at": now,
        }

        self._events_table.put_item(Item=chain_record)
        return chain_record

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit_pair_ready(self, pair_record: dict) -> None:
        """Emit FilingPairReady event to the output queue."""
        if not self._output_queue_url:
            logger.debug("No output queue configured — skipping FilingPairReady emission")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingPairReady(
            timestamp=now,
            source="filing_indexer",
            trace_id=str(uuid.uuid4()),
            payload={
                "entity_id": pair_record["entity_id"],
                "form_type": pair_record["form_type"],
                "current_filing_date": pair_record["current_filing_date"],
                "prior_filing_date": pair_record["prior_filing_date"],
                "current_s3_prefix": pair_record["current_s3_prefix"],
                "prior_s3_prefix": pair_record["prior_s3_prefix"],
                "pair_id": pair_record["pair_id"],
            },
        )
        self._sqs.send_message(
            QueueUrl=self._output_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def _emit_pair_ready_to_delta(self, pair_record: dict) -> None:
        """Fan-out: emit FilingPairReady event to the delta-analysis queue."""
        if not self._delta_analysis_queue_url:
            logger.debug("No delta analysis queue configured — skipping fan-out")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingPairReady(
            timestamp=now,
            source="filing_indexer",
            trace_id=str(uuid.uuid4()),
            payload={
                "entity_id": pair_record["entity_id"],
                "form_type": pair_record["form_type"],
                "current_filing_date": pair_record["current_filing_date"],
                "prior_filing_date": pair_record["prior_filing_date"],
                "current_s3_prefix": pair_record["current_s3_prefix"],
                "prior_s3_prefix": pair_record["prior_s3_prefix"],
                "pair_id": pair_record["pair_id"],
            },
        )
        self._sqs.send_message(
            QueueUrl=self._delta_analysis_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def _emit_chain_ready(self, chain_record: dict) -> None:
        """Emit FilingChainReady event to the output queue."""
        if not self._output_queue_url:
            logger.debug("No output queue configured — skipping FilingChainReady emission")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingChainReady(
            timestamp=now,
            source="filing_indexer",
            trace_id=str(uuid.uuid4()),
            payload={
                "entity_id": chain_record["entity_id"],
                "form_type": chain_record["form_type"],
                "chain_length": chain_record["chain_length"],
                "latest_filing_date": chain_record["latest_filing_date"],
                "filing_dates": chain_record["filing_dates"],
                "chain_id": chain_record["chain_id"],
            },
        )
        self._sqs.send_message(
            QueueUrl=self._output_queue_url,
            MessageBody=event.to_sqs_message(),
        )
