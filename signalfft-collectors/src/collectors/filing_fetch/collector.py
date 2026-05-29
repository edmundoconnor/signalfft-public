"""Filing document fetcher — downloads SEC filing HTML and stores in S3."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import boto3
import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _IndexTableParser(HTMLParser):
    """Parse the SEC filing index HTML table to find the primary document link.

    Looks for table rows with an .htm link that is not the index page itself.
    """

    def __init__(self):
        super().__init__()
        self._in_td = False
        self._in_a = False
        self._current_href: str | None = None
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._in_td = True
        elif tag == "a" and self._in_td:
            self._in_a = True
            for name, value in attrs:
                if name == "href" and value:
                    self._current_href = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._in_td = False
            self._in_a = False
            self._current_href = None
        elif tag == "a":
            self._in_a = False

    def handle_data(self, data: str) -> None:
        if self._in_a and self._current_href:
            href = self._current_href
            if href.endswith(".htm") or href.endswith(".html"):
                if "index" not in href.lower():
                    self.documents.append(href)
                    self._current_href = None


class FilingFetcher:
    """Fetches SEC filing documents and stores them in S3."""

    def __init__(self):
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")
        self._s3 = boto3.client("s3", region_name=self._region)
        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo_resource = boto3.resource("dynamodb", region_name=self._region)

        self._bucket = (
            os.environ.get("ARTIFACTS_BUCKET")
            or os.environ.get("ARTIFACT_BUCKET")
            or f"{self._env}-signalfft-artifacts"
        )
        self._events_table = self._dynamo_resource.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )
        self._filing_ready_queue_url = os.environ.get("FILING_READY_QUEUE_URL", "")
        self._user_agent = os.environ.get(
            "EDGAR_USER_AGENT",
            "SignalFFT/0.1 (contact@example.com)",
        )

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json",
        })

        self._last_request_time = 0.0

    def _fetch_with_rate_limit(self, url: str) -> requests.Response:
        """Fetch a URL with SEC rate limiting (100ms between requests).

        Retries with exponential backoff on 429/503.
        """
        # Enforce 100ms inter-request delay
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)

        backoff_delays = [1, 2, 4]

        for attempt in range(len(backoff_delays) + 1):
            self._last_request_time = time.monotonic()
            logger.info("SEC request: %s (attempt %d)", url, attempt + 1)
            response = self._session.get(url, timeout=30)

            if response.status_code in (429, 503):
                if attempt < len(backoff_delays):
                    delay = backoff_delays[attempt]
                    logger.warning(
                        "SEC rate limited (%d), backing off %ds",
                        response.status_code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                # Exhausted retries
                response.raise_for_status()

            return response

        # Should not reach here, but satisfy type checker
        return response  # type: ignore[possibly-undefined]

    def _find_primary_document(self, filing_url: str) -> str | None:
        """Find the primary document URL from a filing index page.

        Tries JSON index first, falls back to HTML table parsing.
        """
        # Derive base URL from the filing index URL
        # filing_url is like: https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/ADSH-index.htm
        base_url = filing_url.rsplit("/", 1)[0]

        # Try JSON index first
        json_url = f"{base_url}/index.json"
        try:
            response = self._fetch_with_rate_limit(json_url)
            if response.status_code == 200:
                data = response.json()
                items = data.get("directory", {}).get("item", [])
                for item in items:
                    name = item.get("name", "")
                    if (name.endswith(".htm") or name.endswith(".html")) and "index" not in name.lower():
                        return f"{base_url}/{name}"
        except Exception:
            logger.debug("JSON index failed for %s, trying HTML fallback", filing_url)

        # Fall back to HTML index page
        try:
            response = self._fetch_with_rate_limit(filing_url)
            if response.status_code == 200:
                parser = _IndexTableParser()
                parser.feed(response.text)
                if parser.documents:
                    doc_path = parser.documents[0]
                    if doc_path.startswith("http"):
                        return doc_path
                    return f"{base_url}/{doc_path}"
        except Exception:
            logger.exception("HTML index parsing failed for %s", filing_url)

        return None

    def _store_filing(self, cik: str, form_type: str, filing_date: str, content: str) -> str:
        """Store filing content in S3 and return the S3 URI."""
        # Normalize form type: spaces -> underscores
        normalized_form = form_type.replace(" ", "_")
        key = f"filings/{cik}/{normalized_form}/{filing_date}/raw.html"
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/html",
        )
        return f"s3://{self._bucket}/{key}"

    def _store_filing_with_retry(self, cik: str, form_type: str, filing_date: str, content: str) -> str:
        """Store filing with up to 3 retries on S3 failure."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._store_filing(cik, form_type, filing_date, content)
            except Exception as e:
                last_error = e
                logger.warning("S3 store attempt %d failed: %s", attempt + 1, e)
        raise last_error  # type: ignore[misc]

    def _update_event_record(self, event_id: str, entity_id: str, filing_s3_uri: str) -> None:
        """Update the event record in DynamoDB with the filing S3 URI."""
        from signalfft_common.dynamo.keys import build_events_pk

        pk = build_events_pk(entity_id)

        # Query for the event by PK, filter by event_id
        response = self._events_table.query(
            KeyConditionExpression="PK = :pk",
            FilterExpression="event_id = :eid",
            ExpressionAttributeValues={
                ":pk": pk,
                ":eid": event_id,
            },
        )
        items = response.get("Items", [])
        if not items:
            logger.warning("Event record not found: event_id=%s entity_id=%s", event_id, entity_id)
            return

        item = items[0]
        self._events_table.update_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            UpdateExpression="SET filing_s3_uri = :uri",
            ExpressionAttributeValues={":uri": filing_s3_uri},
        )

    def _mark_fetch_failed(self, event_id: str, entity_id: str) -> None:
        """Mark the event record as having a failed filing fetch."""
        from signalfft_common.dynamo.keys import build_events_pk

        pk = build_events_pk(entity_id)

        response = self._events_table.query(
            KeyConditionExpression="PK = :pk",
            FilterExpression="event_id = :eid",
            ExpressionAttributeValues={
                ":pk": pk,
                ":eid": event_id,
            },
        )
        items = response.get("Items", [])
        if not items:
            logger.warning("Event record not found for failure mark: event_id=%s", event_id)
            return

        item = items[0]
        self._events_table.update_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            UpdateExpression="SET filing_fetch_status = :status",
            ExpressionAttributeValues={":status": "FILING_FETCH_FAILED"},
        )

    def _emit_filing_ready(
        self, event_id: str, entity_id: str, filing_s3_uri: str,
        form_type: str, filing_date: str, cik: str,
    ) -> None:
        """Publish FilingDocumentReady to the filing-ready queue."""
        if not self._filing_ready_queue_url:
            return

        from signalfft_common.events import FilingDocumentReady

        event = FilingDocumentReady(
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="FILING_FETCHER",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "filing_s3_uri": filing_s3_uri,
                "form_type": form_type,
                "filing_date": filing_date,
                "cik": cik,
            },
        )
        self._sqs.send_message(
            QueueUrl=self._filing_ready_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def process_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Process a single SQS message containing a FilingDocumentRequested event.

        Returns a stats dict: {status, event_id}.
        """
        body = json.loads(message.get("body", message.get("Body", "{}")))
        payload = body.get("payload", {})

        event_id = payload.get("event_id", "")
        entity_id = payload.get("entity_id", "")
        filing_url = payload.get("filing_url", "")
        form_type = payload.get("form_type", "")
        filing_date = payload.get("filing_date", "")
        cik = payload.get("cik", "")

        logger.info("Processing filing fetch: event_id=%s filing_url=%s", event_id, filing_url)

        # 1. Find primary document URL
        doc_url = self._find_primary_document(filing_url)
        if not doc_url:
            logger.error("No primary document found for %s", filing_url)
            self._mark_fetch_failed(event_id, entity_id)
            return {"status": "no_document", "event_id": event_id}

        # 2. Download the filing
        try:
            response = self._fetch_with_rate_limit(doc_url)
            if response.status_code == 404:
                logger.error("Filing document 404: %s", doc_url)
                self._mark_fetch_failed(event_id, entity_id)
                return {"status": "not_found", "event_id": event_id}
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if hasattr(e, "response") and e.response is not None and e.response.status_code == 404:
                logger.error("Filing document 404: %s", doc_url)
                self._mark_fetch_failed(event_id, entity_id)
                return {"status": "not_found", "event_id": event_id}
            raise

        content = response.text

        # 3. Warn if filing is suspiciously small
        if len(content.encode("utf-8")) < 1024:
            logger.warning("Small filing (%d bytes) for %s", len(content.encode("utf-8")), event_id)

        # 4. Store in S3 with retry
        filing_s3_uri = self._store_filing_with_retry(cik, form_type, filing_date, content)

        # 5. Update event record in DynamoDB
        self._update_event_record(event_id, entity_id, filing_s3_uri)

        # 6. Emit FilingDocumentReady
        self._emit_filing_ready(event_id, entity_id, filing_s3_uri, form_type, filing_date, cik)

        logger.info("Filing stored: %s -> %s", event_id, filing_s3_uri)
        return {"status": "success", "event_id": event_id}


def lambda_handler(event: dict, context: Any) -> dict:
    """Process SQS batch of FilingDocumentRequested messages."""
    fetcher = FilingFetcher()
    records = event.get("Records", [])
    stats = {"processed": 0, "success": 0, "errors": 0}

    for record in records:
        try:
            result = fetcher.process_message(record)
            stats["processed"] += 1
            if result.get("status") == "success":
                stats["success"] += 1
        except Exception:
            logger.exception("Error processing filing fetch message")
            stats["errors"] += 1

    logger.info("Filing fetcher complete: %s", json.dumps(stats))
    return {
        "statusCode": 200,
        "body": json.dumps(stats),
    }
