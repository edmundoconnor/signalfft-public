"""Narrative model -- multi-entity story arcs tracked over time."""

from __future__ import annotations

from dataclasses import dataclass, field

from signalfft_common.enums import NarrativeState


@dataclass(slots=True)
class Narrative:
    """A narrative arc grouping related entities and signals.

    transition_history entries: {from, to, at} dicts.
    """

    narrative_id: str
    lifecycle_state: NarrativeState
    gravity_score: float
    entities: set[str] = field(default_factory=set)
    claude_label: str = ""  # advisory only
    transition_history: list[dict] = field(default_factory=list)
    created_at: str = ""  # ISO 8601
