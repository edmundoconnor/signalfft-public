"""Unit tests for SignalFFT DynamoClient using moto mock_aws."""

from __future__ import annotations

import boto3
from moto import mock_aws

from signalfft_common.dynamo.client import DynamoClient
from signalfft_common.models import Entity


TABLE_NAME = "test-table"
REGION = "us-east-1"


def _create_table_and_client() -> DynamoClient:
    """Helper: create a DynamoDB table and return a DynamoClient wrapping it."""
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
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
    table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    return DynamoClient(TABLE_NAME, region=REGION)


def _make_entity(entity_id: str = "ent-001", name: str = "Acme Corp") -> Entity:
    return Entity(
        entity_id=entity_id,
        entity_type="COMPANY",
        name=name,
        aliases={"ACME", "Acme"},
        created_at="2026-02-15T12:00:00Z",
        updated_at="2026-02-15T12:00:00Z",
    )


# ---------------------------------------------------------------------------
# 1. put_item_from_model + get_item_to_model round-trip
# ---------------------------------------------------------------------------


@mock_aws
def test_put_and_get_entity() -> None:
    client = _create_table_and_client()
    entity = _make_entity()

    client.put_item_from_model(entity, pk="ENTITY#ent-001", sk="META")
    retrieved = client.get_item_to_model(Entity, pk="ENTITY#ent-001", sk="META")

    assert retrieved is not None
    assert retrieved.entity_id == "ent-001"
    assert retrieved.entity_type == "COMPANY"
    assert retrieved.name == "Acme Corp"
    assert retrieved.created_at == "2026-02-15T12:00:00Z"
    assert retrieved.updated_at == "2026-02-15T12:00:00Z"


# ---------------------------------------------------------------------------
# 2. query_by_pk returns correct items
# ---------------------------------------------------------------------------


@mock_aws
def test_query_by_pk() -> None:
    client = _create_table_and_client()

    # Put two items under the same PK but different SKs
    client.put_item_from_model(
        _make_entity("ent-001"), pk="ENTITY#ent-001", sk="META"
    )
    # Simulate an event SK under the same entity PK
    client._table.put_item(
        Item={
            "PK": "ENTITY#ent-001",
            "SK": "EVENT#2026-02-15T12:00:00Z#evt-001",
            "event_id": "evt-001",
        }
    )

    items = client.query_by_pk("ENTITY#ent-001")
    assert len(items) == 2


# ---------------------------------------------------------------------------
# 3. query_by_pk with sk_prefix filters correctly
# ---------------------------------------------------------------------------


@mock_aws
def test_query_by_pk_with_sk_prefix() -> None:
    client = _create_table_and_client()

    # Put items with different SK prefixes under the same PK
    client._table.put_item(
        Item={"PK": "ENTITY#ent-001", "SK": "META", "data": "entity-meta"}
    )
    client._table.put_item(
        Item={
            "PK": "ENTITY#ent-001",
            "SK": "EVENT#2026-02-15T12:00:00Z#evt-001",
            "data": "event-1",
        }
    )
    client._table.put_item(
        Item={
            "PK": "ENTITY#ent-001",
            "SK": "EVENT#2026-02-15T13:00:00Z#evt-002",
            "data": "event-2",
        }
    )
    client._table.put_item(
        Item={
            "PK": "ENTITY#ent-001",
            "SK": "SIGNAL#2026-02-15T12:00:00Z#sig-001",
            "data": "signal-1",
        }
    )

    event_items = client.query_by_pk("ENTITY#ent-001", sk_prefix="EVENT#")
    assert len(event_items) == 2
    assert all(item["SK"].startswith("EVENT#") for item in event_items)

    signal_items = client.query_by_pk("ENTITY#ent-001", sk_prefix="SIGNAL#")
    assert len(signal_items) == 1

    meta_items = client.query_by_pk("ENTITY#ent-001", sk_prefix="META")
    assert len(meta_items) == 1


# ---------------------------------------------------------------------------
# 4. batch_write with 5 items, then verify all 5 are retrievable
# ---------------------------------------------------------------------------


@mock_aws
def test_batch_write() -> None:
    client = _create_table_and_client()

    items = [
        {
            "PK": f"ENTITY#ent-{i:03d}",
            "SK": "META",
            "entity_id": f"ent-{i:03d}",
            "name": f"Entity {i}",
        }
        for i in range(1, 6)
    ]

    client.batch_write(items)

    # Verify each item individually
    for i in range(1, 6):
        response = client._table.get_item(
            Key={"PK": f"ENTITY#ent-{i:03d}", "SK": "META"}
        )
        item = response.get("Item")
        assert item is not None
        assert item["entity_id"] == f"ent-{i:03d}"
        assert item["name"] == f"Entity {i}"


# ---------------------------------------------------------------------------
# 5. get_item_to_model returns None for missing item
# ---------------------------------------------------------------------------


@mock_aws
def test_get_missing_item_returns_none() -> None:
    client = _create_table_and_client()

    result = client.get_item_to_model(Entity, pk="ENTITY#nonexistent", sk="META")
    assert result is None


# ---------------------------------------------------------------------------
# 6. Sets in models are converted to lists when stored
# ---------------------------------------------------------------------------


@mock_aws
def test_sets_converted_to_lists() -> None:
    client = _create_table_and_client()
    entity = _make_entity()

    # Entity has aliases as a set: {"ACME", "Acme"}
    client.put_item_from_model(entity, pk="ENTITY#ent-001", sk="META")

    # Read the raw item from DynamoDB -- aliases should be a list
    response = client._table.get_item(Key={"PK": "ENTITY#ent-001", "SK": "META"})
    raw_item = response["Item"]

    assert isinstance(raw_item["aliases"], list)
    assert sorted(raw_item["aliases"]) == ["ACME", "Acme"]
