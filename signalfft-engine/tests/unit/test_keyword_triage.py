"""Comprehensive tests for keyword triage module."""

from __future__ import annotations

import json
import os
import time
import uuid

import boto3
import pytest
from moto import mock_aws

from engine.feature_extraction.keyword_triage import (
    CATEGORIES,
    CORPORATE_ACTIONS,
    EXECUTIVE_CHANGES,
    FINANCIAL_DISTRESS,
    GUIDANCE_CHANGES,
    LEGAL_REGULATORY,
    TriageResult,
    triage_filing,
)
from signalfft_common.enums import FeatureType
from signalfft_common.events import BaseEvent, HighPriorityFiling


# ===========================================================================
# Category matching tests
# ===========================================================================


class TestExecutiveChanges:
    """Tests for the executive_changes category."""

    def test_resignation_detected(self):
        text = "The CEO announced his resignation effective immediately."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories

    def test_appointed_detected(self):
        text = "The board appointed a new Chief Financial Officer."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories

    def test_interim_ceo_detected(self):
        text = "The company named an interim CEO while conducting a search."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories

    def test_stepped_down_detected(self):
        text = "The chairman stepped down from the board."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories

    def test_succession_detected(self):
        text = "The company announced a succession plan for key executives."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories

    def test_all_executive_terms_matchable(self):
        """Every term in EXECUTIVE_CHANGES should be matchable."""
        for term in EXECUTIVE_CHANGES:
            text = f"The filing mentions {term} in its content."
            result = triage_filing(text)
            assert result.is_high_priority, f"Term '{term}' not detected"
            assert "executive_changes" in result.matched_categories


class TestFinancialDistress:
    """Tests for the financial_distress category."""

    def test_going_concern_detected(self):
        text = "The auditor expressed going concern about the entity."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "financial_distress" in result.matched_categories

    def test_bankruptcy_detected(self):
        text = "The company filed for bankruptcy protection."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "financial_distress" in result.matched_categories

    def test_material_weakness_detected(self):
        text = "Auditors identified a material weakness in internal controls."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "financial_distress" in result.matched_categories

    def test_covenant_breach_detected(self):
        text = "The company disclosed a covenant breach on its credit facility."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "financial_distress" in result.matched_categories

    def test_multi_word_doubt_phrase(self):
        text = "There is substantial doubt about ability to continue as a going concern."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "financial_distress" in result.matched_categories
        terms = [t["term"] for t in result.matched_terms]
        assert "doubt about ability to continue" in terms

    def test_all_distress_terms_matchable(self):
        """Every term in FINANCIAL_DISTRESS should be matchable."""
        for term in FINANCIAL_DISTRESS:
            text = f"The filing mentions {term} in disclosure."
            result = triage_filing(text)
            assert result.is_high_priority, f"Term '{term}' not detected"
            assert "financial_distress" in result.matched_categories


class TestLegalRegulatory:
    """Tests for the legal_regulatory category."""

    def test_sec_investigation_detected(self):
        text = "The company is subject to an SEC investigation."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "legal_regulatory" in result.matched_categories

    def test_subpoena_detected(self):
        text = "The board received a subpoena from the Department of Justice."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "legal_regulatory" in result.matched_categories

    def test_class_action_detected(self):
        text = "A class action lawsuit was filed against the company."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "legal_regulatory" in result.matched_categories

    def test_wells_notice_detected(self):
        text = "The company received a Wells notice from the SEC."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "legal_regulatory" in result.matched_categories

    def test_all_legal_terms_matchable(self):
        """Every term in LEGAL_REGULATORY should be matchable."""
        for term in LEGAL_REGULATORY:
            text = f"The filing discusses {term} in its disclosures."
            result = triage_filing(text)
            assert result.is_high_priority, f"Term '{term}' not detected"
            assert "legal_regulatory" in result.matched_categories


class TestCorporateActions:
    """Tests for the corporate_actions category."""

    def test_merger_detected(self):
        text = "The company announced a merger with a competitor."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "corporate_actions" in result.matched_categories

    def test_tender_offer_detected(self):
        text = "A tender offer was made at $50 per share."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "corporate_actions" in result.matched_categories

    def test_hostile_takeover_detected(self):
        text = "The company is defending against a hostile takeover bid."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "corporate_actions" in result.matched_categories

    def test_spin_off_with_hyphen(self):
        text = "The company completed the spin-off of its retail division."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "corporate_actions" in result.matched_categories

    def test_all_corporate_terms_matchable(self):
        """Every term in CORPORATE_ACTIONS should be matchable."""
        for term in CORPORATE_ACTIONS:
            text = f"The filing announces {term} of business units."
            result = triage_filing(text)
            assert result.is_high_priority, f"Term '{term}' not detected"
            assert "corporate_actions" in result.matched_categories


class TestGuidanceChanges:
    """Tests for the guidance_changes category."""

    def test_withdrew_guidance_detected(self):
        text = "Management withdrew guidance for the fiscal year."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "guidance_changes" in result.matched_categories

    def test_suspended_dividend_detected(self):
        text = "The board suspended dividend payments effective immediately."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "guidance_changes" in result.matched_categories

    def test_lowered_expectations_detected(self):
        text = "The company lowered expectations for full-year revenue."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "guidance_changes" in result.matched_categories

    def test_no_longer_providing_guidance(self):
        text = "Due to uncertainty, the company is no longer providing guidance."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "guidance_changes" in result.matched_categories

    def test_all_guidance_terms_matchable(self):
        """Every term in GUIDANCE_CHANGES should be matchable."""
        for term in GUIDANCE_CHANGES:
            text = f"The company {term} for the upcoming quarter."
            result = triage_filing(text)
            assert result.is_high_priority, f"Term '{term}' not detected"
            assert "guidance_changes" in result.matched_categories


# ===========================================================================
# Priority level tests
# ===========================================================================


class TestPriorityLevels:
    """Tests for priority level assignment logic."""

    def test_no_match_returns_none(self):
        text = "The company held its annual meeting and discussed routine matters."
        result = triage_filing(text)
        assert not result.is_high_priority
        assert result.priority_level == "NONE"
        assert result.category_count == 0
        assert result.matched_categories == []
        assert result.matched_terms == []

    def test_single_corporate_action_is_medium(self):
        text = "The company announced a merger with another firm."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "MEDIUM"
        assert result.category_count == 1

    def test_single_guidance_change_is_medium(self):
        text = "The company withdrew guidance for the fiscal year."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "MEDIUM"
        assert result.category_count == 1

    def test_single_legal_is_medium(self):
        text = "A class action was filed against the company."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "MEDIUM"
        assert result.category_count == 1

    def test_financial_distress_alone_is_high(self):
        text = "The auditor raised going concern doubts."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "HIGH"

    def test_executive_changes_alone_is_high(self):
        text = "The CEO announced his resignation today."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "HIGH"

    def test_two_categories_is_high(self):
        text = "The CEO resigned amid an SEC investigation into the company."
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "HIGH"
        assert result.category_count >= 2

    def test_three_categories_is_high(self):
        text = (
            "The CEO resigned. The company filed for bankruptcy. "
            "A class action lawsuit was also filed."
        )
        result = triage_filing(text)
        assert result.is_high_priority
        assert result.priority_level == "HIGH"
        assert result.category_count >= 3


# ===========================================================================
# Edge cases and matching behavior
# ===========================================================================


class TestEdgeCases:
    """Tests for edge cases and matching behavior."""

    def test_empty_string_returns_no_match(self):
        result = triage_filing("")
        assert not result.is_high_priority
        assert result.priority_level == "NONE"

    def test_whitespace_only_returns_no_match(self):
        result = triage_filing("   \n\t  ")
        assert not result.is_high_priority

    def test_case_insensitive_matching(self):
        text = "The CEO RESIGNED from the company. A BANKRUPTCY filing was made."
        result = triage_filing(text)
        assert result.is_high_priority
        assert "executive_changes" in result.matched_categories
        assert "financial_distress" in result.matched_categories

    def test_matched_terms_have_position(self):
        text = "The company filed for bankruptcy protection."
        result = triage_filing(text)
        assert result.is_high_priority
        for term_info in result.matched_terms:
            assert "term" in term_info
            assert "category" in term_info
            assert "position" in term_info
            assert isinstance(term_info["position"], int)
            assert term_info["position"] >= 0

    def test_matched_terms_lowercased(self):
        text = "The CEO RESIGNED effective immediately."
        result = triage_filing(text)
        terms = [t["term"] for t in result.matched_terms]
        for term in terms:
            assert term == term.lower()

    def test_multiple_matches_same_category(self):
        text = "The CEO resigned. The CFO was also terminated."
        result = triage_filing(text)
        exec_terms = [t for t in result.matched_terms if t["category"] == "executive_changes"]
        assert len(exec_terms) >= 2

    def test_word_boundary_prevents_partial_match(self):
        """'default' should not match 'defaulting' in a non-boundary context... but
        'defaulting' starts with 'default' at a word boundary, so regex \bdefault\b
        will NOT match inside 'defaulting'. This test confirms word boundaries work."""
        text = "The defaults configuration was updated."
        result = triage_filing(text)
        # "defaults" should not match "default" due to word boundary
        distress_terms = [t for t in result.matched_terms if t["category"] == "financial_distress"]
        assert len(distress_terms) == 0

    def test_triage_result_dataclass(self):
        """TriageResult should be a proper dataclass with expected fields."""
        result = TriageResult(is_high_priority=False)
        assert result.is_high_priority is False
        assert result.matched_categories == []
        assert result.matched_terms == []
        assert result.category_count == 0
        assert result.priority_level == "NONE"


# ===========================================================================
# Performance tests
# ===========================================================================


class TestPerformance:
    """Tests for triage performance requirements."""

    def test_large_text_under_50ms(self):
        """Triage of ~100KB text should complete in under 50ms."""
        # Build a ~100KB text block with no keywords (worst case: full scan, no early exit)
        filler = "The company reported quarterly results with no unusual items. " * 2000
        assert len(filler) > 100_000

        start = time.perf_counter()
        result = triage_filing(filler)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert not result.is_high_priority
        assert elapsed_ms < 50, f"Triage took {elapsed_ms:.1f}ms, expected <50ms"

    def test_large_text_with_keywords_under_50ms(self):
        """Even with many matches, triage should complete in under 50ms."""
        segments = [
            "The CEO resigned. ",
            "The company filed for bankruptcy. ",
            "A class action was filed. ",
            "The company announced a merger. ",
            "Management withdrew guidance. ",
        ]
        # Repeat to build ~100KB
        text = "".join(segments * 500)
        assert len(text) > 50_000

        start = time.perf_counter()
        result = triage_filing(text)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result.is_high_priority
        assert result.category_count == 5
        assert elapsed_ms < 50, f"Triage took {elapsed_ms:.1f}ms, expected <50ms"


# ===========================================================================
# Integration with feature extractor
# ===========================================================================


class TestExtractorIntegration:
    """Tests for keyword triage integration with feature extraction."""

    def test_triage_feature_added_when_keywords_present(self):
        from engine.feature_extraction.extractor import extract_features
        content = {"text": "The CEO resigned effective immediately due to an SEC investigation."}
        features = extract_features("evt-t1", "ent-t1", content)
        triage_features = [f for f in features if f.feature_type == FeatureType.TRIAGE]
        assert len(triage_features) == 1
        val = triage_features[0].value
        assert val["priority_level"] == "HIGH"
        assert "executive_changes" in val["matched_categories"]

    def test_no_triage_feature_when_no_keywords(self):
        from engine.feature_extraction.extractor import extract_features
        content = {"text": "The company held its annual shareholder meeting."}
        features = extract_features("evt-t2", "ent-t2", content)
        triage_features = [f for f in features if f.feature_type == FeatureType.TRIAGE]
        assert len(triage_features) == 0

    def test_triage_feature_has_matched_terms(self):
        from engine.feature_extraction.extractor import extract_features
        content = {"text": "The company filed for bankruptcy protection today."}
        features = extract_features("evt-t3", "ent-t3", content)
        triage_features = [f for f in features if f.feature_type == FeatureType.TRIAGE]
        assert len(triage_features) == 1
        val = triage_features[0].value
        assert len(val["matched_terms"]) > 0
        assert val["matched_terms"][0]["term"] == "bankruptcy"

    def test_triage_feature_preserves_ids(self):
        from engine.feature_extraction.extractor import extract_features
        content = {"text": "The CEO resigned and a merger was announced."}
        features = extract_features("evt-t4", "ent-t4", content)
        triage_features = [f for f in features if f.feature_type == FeatureType.TRIAGE]
        assert len(triage_features) == 1
        assert triage_features[0].event_id == "evt-t4"
        assert triage_features[0].entity_id == "ent-t4"


# ===========================================================================
# HighPriorityFiling event schema tests
# ===========================================================================


class TestHighPriorityFilingEvent:
    """Tests for the HighPriorityFiling event schema."""

    def test_event_creation(self):
        event = HighPriorityFiling(
            timestamp="2026-02-25T00:00:00+00:00",
            source="feature_extraction",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": "evt-hp1",
                "entity_id": "AAPL",
                "priority_level": "HIGH",
                "matched_categories": ["executive_changes", "financial_distress"],
                "matched_terms": [{"term": "resigned", "category": "executive_changes", "position": 10}],
            },
        )
        assert event.event_type == "HIGH_PRIORITY_FILING"

    def test_event_serialization_roundtrip(self):
        event = HighPriorityFiling(
            timestamp="2026-02-25T00:00:00+00:00",
            source="feature_extraction",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": "evt-hp2",
                "entity_id": "BSX",
                "priority_level": "MEDIUM",
                "matched_categories": ["corporate_actions"],
                "matched_terms": [{"term": "merger", "category": "corporate_actions", "position": 5}],
            },
        )
        serialized = event.to_sqs_message()
        deserialized = BaseEvent.from_sqs_message(serialized)
        assert isinstance(deserialized, HighPriorityFiling)
        assert deserialized.payload["event_id"] == "evt-hp2"
        assert deserialized.payload["priority_level"] == "MEDIUM"

    def test_event_missing_required_fields(self):
        with pytest.raises(Exception):
            HighPriorityFiling(
                timestamp="2026-02-25T00:00:00+00:00",
                source="feature_extraction",
                trace_id=str(uuid.uuid4()),
                payload={"event_id": "evt-hp3"},  # missing required fields
            )

    def test_event_in_registry(self):
        from signalfft_common.events.schemas import EVENT_TYPE_REGISTRY
        assert "HIGH_PRIORITY_FILING" in EVENT_TYPE_REGISTRY
        assert EVENT_TYPE_REGISTRY["HIGH_PRIORITY_FILING"] is HighPriorityFiling


# ===========================================================================
# Service integration tests (moto mocked AWS)
# ===========================================================================


@pytest.fixture
def aws_env_with_hp_queue():
    """AWS env with high-priority queue for service integration tests."""
    with mock_aws():
        region = "us-east-1"
        env = "test"
        os.environ["AWS_REGION"] = region
        os.environ["ENVIRONMENT"] = env
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        bucket = f"{env}-signalfft-artifacts"
        os.environ["ARTIFACT_BUCKET"] = bucket

        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=bucket)

        sqs = boto3.client("sqs", region_name=region)
        input_q = sqs.create_queue(QueueName="test-raw-events")
        output_q = sqs.create_queue(QueueName="test-features")
        hp_q = sqs.create_queue(QueueName="test-high-priority")
        os.environ["RAW_EVENTS_QUEUE_URL"] = input_q["QueueUrl"]
        os.environ["FEATURES_QUEUE_URL"] = output_q["QueueUrl"]
        os.environ["HIGH_PRIORITY_QUEUE_URL"] = hp_q["QueueUrl"]

        dynamodb = boto3.client("dynamodb", region_name=region)
        table_name = f"{env}-signalfft-features"
        os.environ["FEATURES_TABLE"] = table_name
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "region": region,
            "bucket": bucket,
            "input_queue_url": input_q["QueueUrl"],
            "output_queue_url": output_q["QueueUrl"],
            "hp_queue_url": hp_q["QueueUrl"],
            "table_name": table_name,
        }


def _upload_artifact(aws_env, key: str, content: dict) -> str:
    s3 = boto3.client("s3", region_name=aws_env["region"])
    s3.put_object(
        Bucket=aws_env["bucket"],
        Key=key,
        Body=json.dumps(content).encode("utf-8"),
    )
    return f"s3://{aws_env['bucket']}/{key}"


def _make_raw_event_message(event_id: str, entity_id: str, s3_uri: str) -> dict:
    from signalfft_common.events import RawEventCollected
    event = RawEventCollected(
        timestamp="2026-01-15T00:00:00+00:00",
        source="test-collector",
        trace_id=str(uuid.uuid4()),
        payload={
            "event_id": event_id,
            "entity_id": entity_id,
            "source": "EDGAR",
            "content_hash": "abc123",
            "raw_artifact_s3": s3_uri,
        },
    )
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": "test-receipt-handle",
        "Body": event.to_sqs_message(),
    }


class TestServiceHighPriorityPublish:
    """Tests that the service publishes HighPriorityFiling events."""

    def test_high_priority_event_published_on_triage_match(self, aws_env_with_hp_queue):
        from engine.feature_extraction.service import FeatureExtractionService
        service = FeatureExtractionService()

        content = {"text": "The CEO resigned amid bankruptcy concerns."}
        s3_uri = _upload_artifact(aws_env_with_hp_queue, "events/hp-test.json", content)
        message = _make_raw_event_message("evt-hp-svc", "AAPL", s3_uri)

        service.process_message(message)

        # Check high-priority queue
        sqs = boto3.client("sqs", region_name=aws_env_with_hp_queue["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env_with_hp_queue["hp_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1

        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert isinstance(event, HighPriorityFiling)
        assert event.payload["event_id"] == "evt-hp-svc"
        assert event.payload["entity_id"] == "AAPL"
        assert event.payload["priority_level"] == "HIGH"
        assert "executive_changes" in event.payload["matched_categories"]

    def test_no_high_priority_event_when_no_triage_match(self, aws_env_with_hp_queue):
        from engine.feature_extraction.service import FeatureExtractionService
        service = FeatureExtractionService()

        content = {"text": "The company held its annual shareholder meeting."}
        s3_uri = _upload_artifact(aws_env_with_hp_queue, "events/no-hp-test.json", content)
        message = _make_raw_event_message("evt-nohp", "AAPL", s3_uri)

        service.process_message(message)

        # High-priority queue should be empty
        sqs = boto3.client("sqs", region_name=aws_env_with_hp_queue["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env_with_hp_queue["hp_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 0

    def test_triage_feature_stored_in_dynamo(self, aws_env_with_hp_queue):
        from engine.feature_extraction.service import FeatureExtractionService
        service = FeatureExtractionService()

        content = {"text": "The company filed for bankruptcy protection."}
        s3_uri = _upload_artifact(aws_env_with_hp_queue, "events/dynamo-hp-test.json", content)
        message = _make_raw_event_message("evt-dyn-hp", "BSX", s3_uri)

        service.process_message(message)

        # Verify TRIAGE feature in DynamoDB
        dynamo = boto3.resource("dynamodb", region_name=aws_env_with_hp_queue["region"])
        table = dynamo.Table(aws_env_with_hp_queue["table_name"])
        response = table.scan()
        items = response["Items"]
        triage_items = [i for i in items if i.get("feature_type") == "TRIAGE"]
        assert len(triage_items) == 1
        assert triage_items[0]["entity_id"] == "BSX"
