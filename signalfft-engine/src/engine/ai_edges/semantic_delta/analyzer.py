"""Claude-based semantic delta analyzer for sequential SEC filing comparison.

Async module that calls the Claude API for structured shift detection.
This is a LIBRARY — not a service. It gets called by the SemanticDeltaService.
Pattern: engine/directional/claude_interpreter.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_MAX_INPUT_CHARS = 24_000  # per section (~6000 tokens each, budget for two)
_API_TIMEOUT = 60  # seconds (longer than single-section — two sections)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_MAX_SHIFTS = 20

# Sonnet pricing (USD per million tokens)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0

VALID_SHIFT_TYPES = frozenset({
    "risk_escalation", "risk_removal", "tone_shift",
    "guidance_change", "disclosure_addition", "disclosure_removal",
})

VALID_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})


# ---------------------------------------------------------------------------
# SemanticShift dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SemanticShift:
    """A single detected shift between two filing versions."""

    shift_type: str
    description: str
    severity: int  # 1-5
    direction: str  # bullish, bearish, neutral
    evidence: dict[str, str]


# ---------------------------------------------------------------------------
# Prompt loading and versioning
# ---------------------------------------------------------------------------

_prompt_cache: dict[str, Any] | None = None
_prompt_version_cache: str | None = None


def _resolve_prompt_path() -> Path:
    """Resolve path to the prompt YAML template."""
    env_path = os.environ.get("DELTA_PROMPT_PATH", "")
    if env_path:
        return Path(env_path)
    # Development: resolve relative to this file
    engine_root = Path(__file__).resolve().parents[4]  # signalfft-engine/
    return engine_root.parent / "signalfft-opus" / "prompts" / "semantic_delta_analysis.yaml"


def _load_prompt_template() -> dict[str, Any]:
    """Load and cache the prompt YAML template."""
    global _prompt_cache, _prompt_version_cache
    if _prompt_cache is not None:
        return _prompt_cache

    path = _resolve_prompt_path()
    raw = path.read_text(encoding="utf-8")
    _prompt_version_cache = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    _prompt_cache = yaml.safe_load(raw)
    return _prompt_cache


def get_prompt_version() -> str:
    """Get the SHA-256 hash (first 12 chars) of the prompt template."""
    if _prompt_version_cache is None:
        _load_prompt_template()
    return _prompt_version_cache  # type: ignore[return-value]


def clear_prompt_cache() -> None:
    """Clear cached prompt (for testing)."""
    global _prompt_cache, _prompt_version_cache
    _prompt_cache = None
    _prompt_version_cache = None


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

_TRUNCATION_MARKER = "\n\n[... middle section truncated for length ...]\n\n"


def truncate_text(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """Truncate text to max_chars, keeping beginning and end."""
    if len(text) <= max_chars:
        return text

    usable = max_chars - len(_TRUNCATION_MARKER)
    half = usable // 2
    return text[:half] + _TRUNCATION_MARKER + text[-half:]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(
    current_text: str,
    previous_text: str,
    entity_id: str,
    form_type: str,
    section_name: str,
    current_date: str,
    previous_date: str,
) -> tuple[str, str]:
    """Build system and user prompts from the YAML template.

    Returns (system_prompt, user_prompt).
    """
    template = _load_prompt_template()

    system_prompt = template["system"].strip()

    truncated_current = truncate_text(current_text)
    truncated_previous = truncate_text(previous_text)

    user_prompt = template["user_template"].format(
        entity_id=entity_id,
        form_type=form_type,
        section_name=section_name,
        current_date=current_date,
        previous_date=previous_date,
        current_text=truncated_current,
        previous_text=truncated_previous,
    )

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_response(raw_text: str) -> dict:
    """Parse Claude's JSON response, stripping any markdown fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    return json.loads(text)


def validate_shifts(data: dict) -> list[SemanticShift]:
    """Validate and filter shifts from Claude's response."""
    raw_shifts = data.get("shifts", [])
    if not isinstance(raw_shifts, list):
        return []

    validated: list[SemanticShift] = []

    for raw in raw_shifts[:_MAX_SHIFTS]:
        if not isinstance(raw, dict):
            continue

        shift_type = raw.get("shift_type", "")
        if shift_type not in VALID_SHIFT_TYPES:
            continue

        direction = raw.get("direction", "neutral")
        if direction not in VALID_DIRECTIONS:
            direction = "neutral"

        severity = raw.get("severity", 1)
        try:
            severity = int(severity)
        except (ValueError, TypeError):
            severity = 1
        severity = max(1, min(5, severity))

        description = str(raw.get("description", ""))

        evidence = raw.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        evidence = {
            "previous_excerpt": str(evidence.get("previous_excerpt", "")),
            "current_excerpt": str(evidence.get("current_excerpt", "")),
        }

        validated.append(SemanticShift(
            shift_type=shift_type,
            description=description,
            severity=severity,
            direction=direction,
            evidence=evidence,
        ))

    return validated


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

def log_cost(
    entity_id: str,
    section_name: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float:
    """Log API call with token usage and estimated cost. Returns cost."""
    input_cost = (input_tokens * _INPUT_PRICE_PER_M) / 1_000_000
    output_cost = (output_tokens * _OUTPUT_PRICE_PER_M) / 1_000_000
    total_cost = input_cost + output_cost

    logger.info(
        "Delta analysis cost: entity=%s section=%s model=%s "
        "input_tokens=%d output_tokens=%d estimated_cost=$%.6f",
        entity_id, section_name, model,
        input_tokens, output_tokens, total_cost,
    )
    return total_cost


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def analyze_delta(
    current_text: str,
    previous_text: str,
    entity_id: str,
    form_type: str,
    section_name: str,
    current_date: str,
    previous_date: str,
) -> list[SemanticShift]:
    """Compare two filing sections using Claude and return detected shifts.

    On failure, returns empty list (safe default).
    """
    model = os.environ.get("CLAUDE_MODEL_ID", _DEFAULT_MODEL)

    system_prompt, user_prompt = build_prompt(
        current_text, previous_text, entity_id,
        form_type, section_name, current_date, previous_date,
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning empty shifts")
        return []

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=_API_TIMEOUT,
        max_retries=0,
    )

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            log_cost(
                entity_id, section_name,
                response.usage.input_tokens,
                response.usage.output_tokens,
                model,
            )

            raw_text = response.content[0].text
            parsed = parse_response(raw_text)
            return validate_shifts(parsed)

        except json.JSONDecodeError as exc:
            logger.warning(
                "Malformed JSON from Claude for delta %s/%s: %s",
                entity_id, section_name, exc,
            )
            return []

        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Claude API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                await asyncio.sleep(wait)

    logger.error(
        "Claude API failed after %d attempts for delta %s/%s: %s",
        _MAX_RETRIES, entity_id, section_name, last_exc,
    )
    return []
