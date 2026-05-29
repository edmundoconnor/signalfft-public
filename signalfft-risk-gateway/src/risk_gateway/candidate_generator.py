"""
Generates trade candidates from scored signals. Pure function — no I/O.
"""
import uuid
from datetime import datetime, timezone


def generate_candidates(
    signals: list[dict],
    top_n: int = 10,
    min_score: float = 0.05,
) -> list[dict]:
    """
    Filter signals by min_score, sort by score descending, take top_n,
    and create candidate dicts.
    """
    filtered = [s for s in signals if s.get("score", 0) >= min_score]
    filtered.sort(key=lambda s: s.get("score", 0), reverse=True)
    top = filtered[:top_n]

    now = datetime.now(timezone.utc).isoformat()
    candidates = []
    for sig in top:
        candidates.append({
            "candidate_id": str(uuid.uuid4()),
            "signal_id": sig.get("signal_id", ""),
            "entity_id": sig.get("entity_id", ""),
            "score": sig.get("score", 0),
            "direction_score": sig.get("direction_score", 0.0),
            "risk_status": "PENDING",
            "risk_rejection_reason": None,
            "created_at": now,
        })
    return candidates
