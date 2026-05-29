"""Tests for the Memory Graph query module using moto to mock DynamoDB."""

from __future__ import annotations

import logging

import boto3
import pytest
from moto import mock_aws

from engine.memory_graph.writer import GraphWriter
from engine.memory_graph.query import GraphQuery

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


def _populate_test_graph():
    """Populate the test graph per the spec."""
    w = GraphWriter(table_name=TABLE_NAME, region=REGION)
    w.link_entity_event("AAPL", "EVT_1")
    w.link_entity_signal("AAPL", "SIG_1", metadata={"score": "0.14"})
    w.link_entity_signal("AAPL", "SIG_2", metadata={"score": "0.08"})
    w.link_signal_outcome("SIG_1", "OUT_1")
    w.link_signal_wave("SIG_1", "WAV_1")
    w.link_entity_narrative("AAPL", "NAR_1")
    w.link_entity_event("MSFT", "EVT_2")
    w.link_entity_signal("MSFT", "SIG_3", metadata={"score": "0.11"})


@mock_aws
def test_get_neighbors():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    neighbors = gq.get_neighbors("AAPL")
    target_ids = {n["target_id"] for n in neighbors}
    assert target_ids == {"EVT_1", "SIG_1", "SIG_2", "NAR_1"}


@mock_aws
def test_get_neighbors_filtered():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    neighbors = gq.get_neighbors("AAPL", edge_type="ENTITY_HAS_SIGNAL")
    target_ids = {n["target_id"] for n in neighbors}
    assert target_ids == {"SIG_1", "SIG_2"}


@mock_aws
def test_get_reverse_neighbors():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    # SIG_1 has a reverse edge from AAPL (ENTITY_HAS_SIGNAL)
    rev = gq.get_reverse_neighbors("SIG_1", edge_type="ENTITY_HAS_SIGNAL")
    source_ids = {n["target_id"] for n in rev}
    assert "AAPL" in source_ids


@mock_aws
def test_k_hop_1():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    result = gq.k_hop_neighborhood("AAPL", k=1)
    assert result["root"] == "AAPL"
    # Depth-1 nodes: EVT_1, SIG_1, SIG_2, NAR_1
    depth_1_nodes = {nid for nid, info in result["nodes"].items() if info["depth"] == 1}
    assert depth_1_nodes == {"EVT_1", "SIG_1", "SIG_2", "NAR_1"}
    # Root should not be at depth 1
    assert result["nodes"]["AAPL"]["depth"] == 0


@mock_aws
def test_k_hop_2():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    result = gq.k_hop_neighborhood("AAPL", k=2)
    all_node_ids = set(result["nodes"].keys())
    # At depth 2, we should reach WAV_1 and OUT_1 (through SIG_1)
    assert "WAV_1" in all_node_ids
    assert "OUT_1" in all_node_ids
    assert result["nodes"]["WAV_1"]["depth"] == 2
    assert result["nodes"]["OUT_1"]["depth"] == 2


@mock_aws
def test_k_hop_max_nodes(caplog):
    _create_table()
    w = GraphWriter(table_name=TABLE_NAME, region=REGION)
    # Create a star graph: HUB → 510 leaf nodes
    for i in range(510):
        w.upsert_edge("HUB", "NODE", f"LEAF_{i}", "NODE", "CONNECTED")

    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)
    with caplog.at_level(logging.WARNING):
        result = gq.k_hop_neighborhood("HUB", k=2)

    # Should have hit the 500 node limit
    assert len(result["nodes"]) <= 500
    assert "500 node limit" in caplog.text


@mock_aws
def test_get_entity_signals():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    signals = gq.get_entity_signals("AAPL")
    target_ids = {s["target_id"] for s in signals}
    assert target_ids == {"SIG_1", "SIG_2"}
    assert all(s["edge_type"] == "ENTITY_HAS_SIGNAL" for s in signals)


@mock_aws
def test_get_entity_outcomes():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    outcomes = gq.get_entity_outcomes("AAPL")
    outcome_ids = {o["target_id"] for o in outcomes}
    assert "OUT_1" in outcome_ids


@mock_aws
def test_get_entity_pattern_score_with_history():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    score = gq.get_entity_pattern_score("AAPL")
    # 2 signals, 1 meaningful (SIG_1 score=0.14 > 0.1), SIG_2 score=0.08 not meaningful
    # signal_density = 1/2 = 0.5
    # 1 outcome (OUT_1) via SIG_1
    # outcome_factor = min(1/2, 1.0) = 0.5
    # pattern_score = 0.5 * 0.6 + 0.5 * 0.4 = 0.3 + 0.2 = 0.5
    assert score == pytest.approx(0.5, abs=1e-9)


@mock_aws
def test_get_entity_pattern_score_no_history():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    score = gq.get_entity_pattern_score("UNKNOWN_ENTITY")
    assert score == 0.0


@mock_aws
def test_get_entity_pattern_score_no_outcomes():
    _create_table()
    _populate_test_graph()
    gq = GraphQuery(table_name=TABLE_NAME, region=REGION)

    score = gq.get_entity_pattern_score("MSFT")
    # 1 signal (SIG_3 score=0.11 > 0.1), meaningful=1
    # signal_density = 1/1 = 1.0
    # 0 outcomes → outcome_factor = 0.0
    # pattern_score = 1.0 * 0.6 + 0.0 * 0.4 = 0.6
    assert score == pytest.approx(0.6, abs=1e-9)
