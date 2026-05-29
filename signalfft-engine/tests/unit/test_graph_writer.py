"""Tests for the Memory Graph writer using moto to mock DynamoDB."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from engine.memory_graph.writer import GraphWriter

TABLE_NAME = "test-graph-edges"
REGION = "us-east-1"


def _create_table():
    """Create the graph_edges DynamoDB table with reverse-lookup GSI."""
    client = boto3.client("dynamodb", region_name=REGION)
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "reverse-lookup",
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _get_item(pk: str, sk: str) -> dict | None:
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    resp = table.get_item(Key={"PK": pk, "SK": sk})
    return resp.get("Item")


def _scan_all() -> list[dict]:
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    resp = table.scan()
    return resp.get("Items", [])


@mock_aws
def test_upsert_edge_creates_forward_and_reverse():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.upsert_edge("src1", "ENTITY", "tgt1", "EVENT", "RELATED_TO")

    forward = _get_item("NODE#src1", "EDGE#RELATED_TO#tgt1")
    assert forward is not None
    assert forward["edge_type"] == "RELATED_TO"
    assert forward["target_type"] == "EVENT"
    assert "created_at" in forward

    reverse = _get_item("NODE#tgt1", "EDGE#RELATED_TO#src1")
    assert reverse is not None
    assert reverse["edge_type"] == "RELATED_TO"
    assert reverse["target_type"] == "ENTITY"


@mock_aws
def test_link_entity_event():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.link_entity_event("AAPL", "evt-123")

    forward = _get_item("NODE#AAPL", "EDGE#ENTITY_HAS_EVENT#evt-123")
    assert forward is not None
    assert forward["edge_type"] == "ENTITY_HAS_EVENT"
    assert forward["target_type"] == "EVENT"


@mock_aws
def test_link_entity_signal():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.link_entity_signal("AAPL", "sig-456")

    forward = _get_item("NODE#AAPL", "EDGE#ENTITY_HAS_SIGNAL#sig-456")
    assert forward is not None
    assert forward["edge_type"] == "ENTITY_HAS_SIGNAL"
    assert forward["target_type"] == "SIGNAL"


@mock_aws
def test_on_signal_created():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.on_signal_created("sig-1", "AAPL", "evt-1", score=0.85)

    # entity → event
    assert _get_item("NODE#AAPL", "EDGE#ENTITY_HAS_EVENT#evt-1") is not None
    # entity → signal
    assert _get_item("NODE#AAPL", "EDGE#ENTITY_HAS_SIGNAL#sig-1") is not None
    # signal → event
    assert _get_item("NODE#sig-1", "EDGE#SIGNAL_FROM_EVENT#evt-1") is not None

    # All three should have 6 items total (3 forward + 3 reverse)
    items = _scan_all()
    assert len(items) == 6


@mock_aws
def test_on_wave_created():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.on_wave_created("wave-1", "AAPL", ["sig-1", "sig-2", "sig-3"])

    for sid in ["sig-1", "sig-2", "sig-3"]:
        assert _get_item(f"NODE#{sid}", f"EDGE#SIGNAL_PART_OF_WAVE#wave-1") is not None

    # 3 signals × 2 (forward + reverse) = 6 items
    items = _scan_all()
    assert len(items) == 6


@mock_aws
def test_on_narrative_updated():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.on_narrative_updated("narr-1", ["AAPL", "MSFT", "GOOG"])

    for eid in ["AAPL", "MSFT", "GOOG"]:
        assert _get_item(f"NODE#{eid}", "EDGE#ENTITY_CAPTURED_BY_NARRATIVE#narr-1") is not None

    # 3 entities × 2 = 6 items
    items = _scan_all()
    assert len(items) == 6


@mock_aws
def test_upsert_is_idempotent():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    gw.upsert_edge("src1", "ENTITY", "tgt1", "EVENT", "RELATED_TO")
    gw.upsert_edge("src1", "ENTITY", "tgt1", "EVENT", "RELATED_TO")

    # Same PK/SK, so put_item overwrites — still only 2 items (forward + reverse)
    items = _scan_all()
    assert len(items) == 2


@mock_aws
def test_metadata_stored():
    _create_table()
    gw = GraphWriter(table_name=TABLE_NAME, region=REGION)

    meta = {"score": "0.85", "source": "edgar"}
    gw.upsert_edge("src1", "ENTITY", "tgt1", "EVENT", "RELATED_TO", metadata=meta)

    forward = _get_item("NODE#src1", "EDGE#RELATED_TO#tgt1")
    assert forward["metadata"] == meta

    reverse = _get_item("NODE#tgt1", "EDGE#RELATED_TO#src1")
    assert reverse["metadata"] == meta
