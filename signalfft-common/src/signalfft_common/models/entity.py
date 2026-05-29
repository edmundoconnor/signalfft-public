"""Entity model -- companies, people, sectors tracked by SignalFFT."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Entity:
    """A real-world entity tracked across signals."""

    entity_id: str
    entity_type: str  # COMPANY, PERSON, SECTOR
    name: str
    aliases: set[str] = field(default_factory=set)
    created_at: str = ""  # ISO 8601
    updated_at: str = ""  # ISO 8601
