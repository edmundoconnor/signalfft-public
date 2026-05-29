"""Unit tests for SignalFFT DynamoDB key builder and parser functions."""

from __future__ import annotations

from signalfft_common.dynamo.keys import (
    build_attention_field_pk,
    build_attention_field_sk,
    build_direction_pk,
    build_direction_sk,
    build_entities_pk,
    build_entities_sk,
    build_events_pk,
    build_events_sk,
    build_features_pk,
    build_features_sk,
    build_filing_chains_pk,
    build_filing_chains_sk,
    build_filing_pairs_pk,
    build_filing_pairs_sk,
    build_graph_edges_pk,
    build_graph_edges_sk,
    build_narratives_pk,
    build_narratives_sk,
    build_signals_pk,
    build_signals_sk,
    build_trade_candidates_pk,
    build_trade_candidates_sk,
    build_waves_pk,
    build_waves_sk,
    parse_pk,
    parse_sk,
)

NOW = "2026-02-15T12:00:00Z"


# ---------------------------------------------------------------------------
# 1. entities
# ---------------------------------------------------------------------------


class TestEntitiesKeys:
    def test_pk_format(self) -> None:
        assert build_entities_pk("ent-001") == "ENTITY#ent-001"

    def test_sk_format(self) -> None:
        assert build_entities_sk() == "META"

    def test_pk_round_trip(self) -> None:
        pk = build_entities_pk("ent-001")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "ent-001"

    def test_sk_round_trip(self) -> None:
        sk = build_entities_sk()
        parsed = parse_sk(sk)
        assert parsed["type"] == "META"
        assert "values" not in parsed


# ---------------------------------------------------------------------------
# 2. events
# ---------------------------------------------------------------------------


class TestEventsKeys:
    def test_pk_format(self) -> None:
        assert build_events_pk("ent-001") == "ENTITY#ent-001"

    def test_sk_format(self) -> None:
        assert build_events_sk(NOW, "evt-001") == f"EVENT#{NOW}#evt-001"

    def test_pk_round_trip(self) -> None:
        pk = build_events_pk("ent-002")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "ent-002"

    def test_sk_round_trip(self) -> None:
        sk = build_events_sk(NOW, "evt-001")
        parsed = parse_sk(sk)
        assert parsed["type"] == "EVENT"
        assert parsed["values"] == [NOW, "evt-001"]


# ---------------------------------------------------------------------------
# 3. features
# ---------------------------------------------------------------------------


class TestFeaturesKeys:
    def test_pk_format(self) -> None:
        assert build_features_pk("evt-001") == "EVENT#evt-001"

    def test_sk_format(self) -> None:
        assert build_features_sk("feat-001") == "FEATURE#feat-001"

    def test_pk_round_trip(self) -> None:
        pk = build_features_pk("evt-042")
        parsed = parse_pk(pk)
        assert parsed["type"] == "EVENT"
        assert parsed["id"] == "evt-042"

    def test_sk_round_trip(self) -> None:
        sk = build_features_sk("feat-007")
        parsed = parse_sk(sk)
        assert parsed["type"] == "FEATURE"
        assert parsed["values"] == ["feat-007"]


# ---------------------------------------------------------------------------
# 4. signals
# ---------------------------------------------------------------------------


class TestSignalsKeys:
    def test_pk_format(self) -> None:
        assert build_signals_pk("ent-001") == "ENTITY#ent-001"

    def test_sk_format(self) -> None:
        assert build_signals_sk(NOW, "sig-001") == f"SIGNAL#{NOW}#sig-001"

    def test_pk_round_trip(self) -> None:
        pk = build_signals_pk("ent-005")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "ent-005"

    def test_sk_round_trip(self) -> None:
        sk = build_signals_sk(NOW, "sig-001")
        parsed = parse_sk(sk)
        assert parsed["type"] == "SIGNAL"
        assert parsed["values"] == [NOW, "sig-001"]


# ---------------------------------------------------------------------------
# 5. waves
# ---------------------------------------------------------------------------


class TestWavesKeys:
    def test_pk_format(self) -> None:
        assert build_waves_pk("ent-001") == "ENTITY#ent-001"

    def test_sk_format(self) -> None:
        assert build_waves_sk(NOW) == f"WAVE#{NOW}"

    def test_pk_round_trip(self) -> None:
        pk = build_waves_pk("ent-010")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "ent-010"

    def test_sk_round_trip(self) -> None:
        sk = build_waves_sk(NOW)
        parsed = parse_sk(sk)
        assert parsed["type"] == "WAVE"
        assert parsed["values"] == [NOW]


# ---------------------------------------------------------------------------
# 6. narratives
# ---------------------------------------------------------------------------


class TestNarrativesKeys:
    def test_pk_format(self) -> None:
        assert build_narratives_pk("nar-001") == "NARRATIVE#nar-001"

    def test_sk_format(self) -> None:
        assert build_narratives_sk(NOW) == f"STATE#{NOW}"

    def test_pk_round_trip(self) -> None:
        pk = build_narratives_pk("nar-042")
        parsed = parse_pk(pk)
        assert parsed["type"] == "NARRATIVE"
        assert parsed["id"] == "nar-042"

    def test_sk_round_trip(self) -> None:
        sk = build_narratives_sk(NOW)
        parsed = parse_sk(sk)
        assert parsed["type"] == "STATE"
        assert parsed["values"] == [NOW]


# ---------------------------------------------------------------------------
# 7. attention_field
# ---------------------------------------------------------------------------


class TestAttentionFieldKeys:
    def test_pk_format(self) -> None:
        assert build_attention_field_pk("af-001") == "FIELD#af-001"

    def test_sk_format(self) -> None:
        assert build_attention_field_sk(NOW) == f"SNAPSHOT#{NOW}"

    def test_pk_round_trip(self) -> None:
        pk = build_attention_field_pk("af-v2")
        parsed = parse_pk(pk)
        assert parsed["type"] == "FIELD"
        assert parsed["id"] == "af-v2"

    def test_sk_round_trip(self) -> None:
        sk = build_attention_field_sk(NOW)
        parsed = parse_sk(sk)
        assert parsed["type"] == "SNAPSHOT"
        assert parsed["values"] == [NOW]


# ---------------------------------------------------------------------------
# 8. trade_candidates
# ---------------------------------------------------------------------------


class TestTradeCandidatesKeys:
    def test_pk_format(self) -> None:
        assert build_trade_candidates_pk("tc-001") == "CANDIDATE#tc-001"

    def test_sk_format(self) -> None:
        assert build_trade_candidates_sk() == "META"

    def test_pk_round_trip(self) -> None:
        pk = build_trade_candidates_pk("tc-abc")
        parsed = parse_pk(pk)
        assert parsed["type"] == "CANDIDATE"
        assert parsed["id"] == "tc-abc"

    def test_sk_round_trip(self) -> None:
        sk = build_trade_candidates_sk()
        parsed = parse_sk(sk)
        assert parsed["type"] == "META"
        assert "values" not in parsed


# ---------------------------------------------------------------------------
# 9. graph_edges
# ---------------------------------------------------------------------------


class TestGraphEdgesKeys:
    def test_pk_format(self) -> None:
        assert build_graph_edges_pk("src-001") == "NODE#src-001"

    def test_sk_format(self) -> None:
        assert (
            build_graph_edges_sk("ENTITY_HAS_EVENT", "tgt-001")
            == "EDGE#ENTITY_HAS_EVENT#tgt-001"
        )

    def test_pk_round_trip(self) -> None:
        pk = build_graph_edges_pk("src-999")
        parsed = parse_pk(pk)
        assert parsed["type"] == "NODE"
        assert parsed["id"] == "src-999"

    def test_sk_round_trip(self) -> None:
        sk = build_graph_edges_sk("SIGNAL_PART_OF_WAVE", "tgt-002")
        parsed = parse_sk(sk)
        assert parsed["type"] == "EDGE"
        assert parsed["values"] == ["SIGNAL_PART_OF_WAVE", "tgt-002"]


# ---------------------------------------------------------------------------
# 10. direction_assessments
# ---------------------------------------------------------------------------


class TestDirectionKeys:
    def test_pk_format(self) -> None:
        assert build_direction_pk("AAPL") == "ENTITY#AAPL"

    def test_sk_format(self) -> None:
        assert build_direction_sk("item_7", "2026-02-15") == "DIRECTION#item_7#2026-02-15"

    def test_pk_round_trip(self) -> None:
        pk = build_direction_pk("MSFT")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "MSFT"

    def test_sk_round_trip(self) -> None:
        sk = build_direction_sk("item_1a", "2026-03-01")
        parsed = parse_sk(sk)
        assert parsed["type"] == "DIRECTION"
        assert parsed["values"] == ["item_1a", "2026-03-01"]


# ---------------------------------------------------------------------------
# 11. filing_pairs
# ---------------------------------------------------------------------------


class TestFilingPairsKeys:
    def test_pk_format(self) -> None:
        assert build_filing_pairs_pk("AAPL") == "ENTITY#AAPL"

    def test_sk_format(self) -> None:
        assert build_filing_pairs_sk("10-K", "2026-02-15") == "PAIR#10-K#2026-02-15"

    def test_pk_round_trip(self) -> None:
        pk = build_filing_pairs_pk("BSX")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "BSX"

    def test_sk_round_trip(self) -> None:
        sk = build_filing_pairs_sk("10-Q", "2026-01-01")
        parsed = parse_sk(sk)
        assert parsed["type"] == "PAIR"
        assert parsed["values"] == ["10-Q", "2026-01-01"]


# ---------------------------------------------------------------------------
# 11. filing_chains
# ---------------------------------------------------------------------------


class TestFilingChainsKeys:
    def test_pk_format(self) -> None:
        assert build_filing_chains_pk("AAPL") == "ENTITY#AAPL"

    def test_sk_format(self) -> None:
        assert build_filing_chains_sk("10-K") == "CHAIN#10-K"

    def test_pk_round_trip(self) -> None:
        pk = build_filing_chains_pk("MSFT")
        parsed = parse_pk(pk)
        assert parsed["type"] == "ENTITY"
        assert parsed["id"] == "MSFT"

    def test_sk_round_trip(self) -> None:
        sk = build_filing_chains_sk("8-K")
        parsed = parse_sk(sk)
        assert parsed["type"] == "CHAIN"
        assert parsed["values"] == ["8-K"]


# ---------------------------------------------------------------------------
# 12. parse_pk extracts correct type and id
# ---------------------------------------------------------------------------


class TestParsePk:
    def test_entity_pk(self) -> None:
        result = parse_pk("ENTITY#ent-123")
        assert result == {"type": "ENTITY", "id": "ent-123"}

    def test_node_pk(self) -> None:
        result = parse_pk("NODE#src-abc")
        assert result == {"type": "NODE", "id": "src-abc"}

    def test_candidate_pk(self) -> None:
        result = parse_pk("CANDIDATE#tc-xyz")
        assert result == {"type": "CANDIDATE", "id": "tc-xyz"}

    def test_pk_with_hash_in_id(self) -> None:
        # Only splits on the first '#'
        result = parse_pk("EVENT#evt#extra")
        assert result == {"type": "EVENT", "id": "evt#extra"}

    def test_pk_no_hash(self) -> None:
        result = parse_pk("META")
        assert result == {"type": "META", "id": ""}

    def test_pk_empty_id(self) -> None:
        result = parse_pk("ENTITY#")
        assert result == {"type": "ENTITY", "id": ""}


# ---------------------------------------------------------------------------
# 11. parse_sk extracts correct type and values
# ---------------------------------------------------------------------------


class TestParseSk:
    def test_meta_sk(self) -> None:
        result = parse_sk("META")
        assert result == {"type": "META"}
        assert "values" not in result

    def test_event_sk(self) -> None:
        result = parse_sk("EVENT#2026-01-01T00:00:00Z#evt-001")
        assert result == {
            "type": "EVENT",
            "values": ["2026-01-01T00:00:00Z", "evt-001"],
        }

    def test_feature_sk(self) -> None:
        result = parse_sk("FEATURE#feat-001")
        assert result == {"type": "FEATURE", "values": ["feat-001"]}

    def test_edge_sk(self) -> None:
        result = parse_sk("EDGE#ENTITY_HAS_EVENT#tgt-001")
        assert result == {
            "type": "EDGE",
            "values": ["ENTITY_HAS_EVENT", "tgt-001"],
        }

    def test_wave_sk(self) -> None:
        result = parse_sk(f"WAVE#{NOW}")
        assert result == {"type": "WAVE", "values": [NOW]}

    def test_snapshot_sk(self) -> None:
        result = parse_sk(f"SNAPSHOT#{NOW}")
        assert result == {"type": "SNAPSHOT", "values": [NOW]}
