"""PK/SK builder and parser functions for SignalFFT DynamoDB tables.

Each table has a pair of builder functions that produce correctly formatted
partition-key and sort-key strings.  Two generic parse helpers allow callers
to decompose any key back into its constituent parts.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# entities  (PK: ENTITY#{entity_id}  SK: META)
# ---------------------------------------------------------------------------

def build_entities_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_entities_sk() -> str:
    return "META"


# ---------------------------------------------------------------------------
# events  (PK: ENTITY#{entity_id}  SK: EVENT#{timestamp}#{event_id})
# ---------------------------------------------------------------------------

def build_events_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_events_sk(timestamp: str, event_id: str) -> str:
    return f"EVENT#{timestamp}#{event_id}"


# ---------------------------------------------------------------------------
# features  (PK: EVENT#{event_id}  SK: FEATURE#{feature_id})
# ---------------------------------------------------------------------------

def build_features_pk(event_id: str) -> str:
    return f"EVENT#{event_id}"


def build_features_sk(feature_id: str) -> str:
    return f"FEATURE#{feature_id}"


# ---------------------------------------------------------------------------
# signals  (PK: ENTITY#{entity_id}  SK: SIGNAL#{timestamp}#{signal_id})
# ---------------------------------------------------------------------------

def build_signals_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_signals_sk(timestamp: str, signal_id: str) -> str:
    return f"SIGNAL#{timestamp}#{signal_id}"


# ---------------------------------------------------------------------------
# waves  (PK: ENTITY#{entity_id}  SK: WAVE#{window_end})
# ---------------------------------------------------------------------------

def build_waves_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_waves_sk(window_end: str) -> str:
    return f"WAVE#{window_end}"


# ---------------------------------------------------------------------------
# narratives  (PK: NARRATIVE#{narrative_id}  SK: STATE#{timestamp})
# ---------------------------------------------------------------------------

def build_narratives_pk(narrative_id: str) -> str:
    return f"NARRATIVE#{narrative_id}"


def build_narratives_sk(timestamp: str) -> str:
    return f"STATE#{timestamp}"


# ---------------------------------------------------------------------------
# attention_field  (PK: FIELD#{field_id}  SK: SNAPSHOT#{timestamp})
# ---------------------------------------------------------------------------

def build_attention_field_pk(field_id: str) -> str:
    return f"FIELD#{field_id}"


def build_attention_field_sk(timestamp: str) -> str:
    return f"SNAPSHOT#{timestamp}"


# ---------------------------------------------------------------------------
# trade_candidates  (PK: CANDIDATE#{candidate_id}  SK: META)
# ---------------------------------------------------------------------------

def build_trade_candidates_pk(candidate_id: str) -> str:
    return f"CANDIDATE#{candidate_id}"


def build_trade_candidates_sk() -> str:
    return "META"


# ---------------------------------------------------------------------------
# outcomes  (PK: ENTITY#{entity_id}  SK: OUTCOME#{signal_id})
# ---------------------------------------------------------------------------

def build_outcomes_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_outcomes_sk(signal_id: str) -> str:
    return f"OUTCOME#{signal_id}"


# ---------------------------------------------------------------------------
# filing_sections  (PK: ENTITY#{entity_id}  SK: SECTIONS#{form_type}#{filing_date})
# ---------------------------------------------------------------------------

def build_filing_sections_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_filing_sections_sk(form_type: str, filing_date: str) -> str:
    return f"SECTIONS#{form_type}#{filing_date}"


# ---------------------------------------------------------------------------
# filing_pairs  (PK: ENTITY#{entity_id}  SK: PAIR#{form_type}#{filing_date})
# ---------------------------------------------------------------------------

def build_filing_pairs_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_filing_pairs_sk(form_type: str, filing_date: str) -> str:
    return f"PAIR#{form_type}#{filing_date}"


# ---------------------------------------------------------------------------
# filing_chains  (PK: ENTITY#{entity_id}  SK: CHAIN#{form_type})
# ---------------------------------------------------------------------------

def build_filing_chains_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_filing_chains_sk(form_type: str) -> str:
    return f"CHAIN#{form_type}"


# ---------------------------------------------------------------------------
# direction_assessments  (PK: ENTITY#{entity_id}  SK: DIRECTION#{section_name}#{filing_date})
# ---------------------------------------------------------------------------

def build_direction_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_direction_sk(section_name: str, filing_date: str) -> str:
    return f"DIRECTION#{section_name}#{filing_date}"


# ---------------------------------------------------------------------------
# triage_assessments  (PK: ENTITY#{entity_id}  SK: TRIAGE#{filing_date}#{event_id})
# ---------------------------------------------------------------------------

def build_triage_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_triage_sk(filing_date: str, event_id: str) -> str:
    return f"TRIAGE#{filing_date}#{event_id}"


# ---------------------------------------------------------------------------
# semantic_deltas  (PK: ENTITY#{entity_id}  SK: DELTA#{filing_date}#{section_name})
# ---------------------------------------------------------------------------

def build_delta_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_delta_sk(filing_date: str, section_name: str) -> str:
    return f"DELTA#{filing_date}#{section_name}"


# ---------------------------------------------------------------------------
# shadow_scores  (PK: ENTITY#{entity_id}  SK: SHADOW#{signal_id}#{edge_name})
# ---------------------------------------------------------------------------

def build_shadow_scores_pk(entity_id: str) -> str:
    return f"ENTITY#{entity_id}"


def build_shadow_scores_sk(signal_id: str, edge_name: str) -> str:
    return f"SHADOW#{signal_id}#{edge_name}"


# ---------------------------------------------------------------------------
# graph_edges  (PK: NODE#{source_id}  SK: EDGE#{edge_type}#{target_id})
# ---------------------------------------------------------------------------

def build_graph_edges_pk(source_id: str) -> str:
    return f"NODE#{source_id}"


def build_graph_edges_sk(edge_type: str, target_id: str) -> str:
    return f"EDGE#{edge_type}#{target_id}"


# ---------------------------------------------------------------------------
# Generic parsers
# ---------------------------------------------------------------------------

def parse_pk(pk: str) -> dict:
    """Parse a PK string into its components.

    Example: ``'ENTITY#abc'`` -> ``{'type': 'ENTITY', 'id': 'abc'}``
    """
    parts = pk.split("#", 1)
    return {"type": parts[0], "id": parts[1] if len(parts) > 1 else ""}


def parse_sk(sk: str) -> dict:
    """Parse an SK string into its components.

    Example: ``'EVENT#2026-01-01#evt-001'`` ->
    ``{'type': 'EVENT', 'values': ['2026-01-01', 'evt-001']}``
    """
    parts = sk.split("#")
    result: dict = {"type": parts[0]}
    if len(parts) > 1:
        result["values"] = parts[1:]
    return result
