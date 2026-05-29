"""Claude-based directional interpretation for SEC filing sections.

Async module that calls the Claude API for nuanced directional assessment.
This is a LIBRARY — not a service. It gets called by other services.
Results are ADVISORY ONLY — they feed into a lookup table (F2.3),
never into scorer.py directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import yaml

from signalfft_common.dynamo.keys import build_direction_pk, build_direction_sk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_MAX_INPUT_CHARS = 32_000  # ~8000 tokens
_API_TIMEOUT = 30  # seconds
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds

# Sonnet pricing (USD per million tokens)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0


# ---------------------------------------------------------------------------
# DirectionAssessment dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DirectionAssessment:
    """Result of Claude directional interpretation.

    Advisory only — never consumed directly by the scorer.
    Maps to numeric values through a deterministic lookup table (F2.3).
    """

    direction: str  # "bullish", "bearish", "neutral"
    confidence: float  # 0.0 to 1.0
    reasoning: str
    key_directional_factors: list[str]
    claude_model_version: str
    prompt_version: str
    entity_id: str
    section_name: str
    filing_date: str
    created_at: str  # ISO 8601


# ---------------------------------------------------------------------------
# Prompt loading and versioning
# ---------------------------------------------------------------------------

_prompt_cache: dict[str, Any] | None = None
_prompt_version_cache: str | None = None


def _resolve_prompt_path() -> Path:
    """Resolve path to the prompt YAML template."""
    env_path = os.environ.get("PROMPT_TEMPLATE_PATH", "")
    if env_path:
        return Path(env_path)
    # Development: resolve relative to this file
    # signalfft-engine/src/engine/directional/claude_interpreter.py
    # -> ../../../../signalfft-opus/prompts/directional_interpretation.yaml
    engine_root = Path(__file__).resolve().parents[3]  # signalfft-engine/
    return engine_root.parent / "signalfft-opus" / "prompts" / "directional_interpretation.yaml"


def _load_prompt_template() -> dict[str, Any]:
    """Load and cache the prompt YAML template."""
    global _prompt_cache, _prompt_version_cache
    if _prompt_cache is not None:
        return _prompt_cache

    path = _resolve_prompt_path()
    raw = path.read_text(encoding="utf-8")
    _prompt_version_cache = _compute_prompt_version(raw)
    _prompt_cache = yaml.safe_load(raw)
    return _prompt_cache


def _get_prompt_version() -> str:
    """Get the SHA-256 hash (first 12 chars) of the prompt template."""
    if _prompt_version_cache is None:
        _load_prompt_template()
    return _prompt_version_cache  # type: ignore[return-value]


def _compute_prompt_version(template_content: str) -> str:
    """Compute SHA-256 hash of prompt template for versioning."""
    return hashlib.sha256(template_content.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

_TRUNCATION_MARKER = "\n\n[... middle section truncated for length ...]\n\n"


def truncate_text(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """Truncate text to max_chars, keeping beginning and end.

    If the text exceeds max_chars, removes the middle section and inserts
    a marker.  Opening statements and concluding remarks are preserved
    since they tend to contain the most important content.
    """
    if len(text) <= max_chars:
        return text

    usable = max_chars - len(_TRUNCATION_MARKER)
    half = usable // 2
    return text[:half] + _TRUNCATION_MARKER + text[-half:]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(
    text: str,
    entity_id: str,
    context: dict,
) -> tuple[str, str]:
    """Build system and user prompts from the YAML template.

    Returns (system_prompt, user_prompt).
    """
    template = _load_prompt_template()

    system_prompt = template["system"].strip()

    truncated = truncate_text(text)
    user_prompt = template["user_template"].format(
        text=truncated,
        entity_id=entity_id,
        form_type=context.get("form_type", "unknown"),
        section_name=context.get("section_name", "unknown"),
        filing_date=context.get("filing_date", "unknown"),
    )

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw_text: str) -> dict:
    """Parse Claude's JSON response, stripping any markdown fences."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    return json.loads(text)


def _validate_response(data: dict) -> dict:
    """Validate parsed response has required fields and valid values."""
    direction = data.get("direction", "neutral")
    if direction not in ("bullish", "bearish", "neutral"):
        direction = "neutral"

    confidence = data.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))

    reasoning = str(data.get("reasoning", ""))

    factors = data.get("key_directional_factors", [])
    if not isinstance(factors, list):
        factors = []
    factors = [str(f) for f in factors[:5]]  # Cap at 5

    return {
        "direction": direction,
        "confidence": confidence,
        "reasoning": reasoning,
        "key_directional_factors": factors,
    }


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

def _log_cost(
    entity_id: str,
    section_name: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> None:
    """Log API call with token usage and estimated cost."""
    input_cost = (input_tokens * _INPUT_PRICE_PER_M) / 1_000_000
    output_cost = (output_tokens * _OUTPUT_PRICE_PER_M) / 1_000_000
    total_cost = input_cost + output_cost

    logger.info(
        "Claude API cost: entity=%s section=%s model=%s "
        "input_tokens=%d output_tokens=%d estimated_cost=$%.6f",
        entity_id, section_name, model,
        input_tokens, output_tokens, total_cost,
    )


# ---------------------------------------------------------------------------
# DynamoDB storage
# ---------------------------------------------------------------------------

def store_assessment(
    assessment: DirectionAssessment,
    events_table: Any,
) -> None:
    """Write a DirectionAssessment to the events DynamoDB table."""
    pk = build_direction_pk(assessment.entity_id)
    sk = build_direction_sk(assessment.section_name, assessment.filing_date)

    item = {
        "PK": pk,
        "SK": sk,
        "direction": assessment.direction,
        "confidence": Decimal(str(assessment.confidence)),
        "reasoning": assessment.reasoning,
        "key_directional_factors": assessment.key_directional_factors,
        "claude_model_version": assessment.claude_model_version,
        "prompt_version": assessment.prompt_version,
        "entity_id": assessment.entity_id,
        "section_name": assessment.section_name,
        "filing_date": assessment.filing_date,
        "created_at": assessment.created_at,
        "source": "claude_interpreter",
    }

    events_table.put_item(Item=item)
    logger.info(
        "Direction assessment stored: entity=%s section=%s direction=%s confidence=%.2f",
        assessment.entity_id, assessment.section_name,
        assessment.direction, assessment.confidence,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def interpret_direction(
    text: str,
    entity_id: str,
    context: dict,
) -> DirectionAssessment:
    """Interpret directional sentiment of a filing section using Claude.

    Parameters
    ----------
    text : str
        The filing section text to analyze.
    entity_id : str
        Ticker symbol (e.g., "AAPL").
    context : dict
        Must contain: form_type, section_name, filing_date.

    Returns
    -------
    DirectionAssessment
        The assessed direction with confidence and reasoning.
        On failure, returns neutral assessment with confidence 0.0.
    """
    section_name = context.get("section_name", "unknown")
    filing_date = context.get("filing_date", "unknown")
    model = os.environ.get("CLAUDE_MODEL_ID", _DEFAULT_MODEL)

    # Build prompt
    system_prompt, user_prompt = build_prompt(text, entity_id, context)
    prompt_version = _get_prompt_version()

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning neutral assessment")
        return _neutral_assessment(entity_id, section_name, filing_date, model, prompt_version)

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=_API_TIMEOUT,
        max_retries=0,  # We handle retries ourselves
    )

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Log cost
            _log_cost(
                entity_id, section_name,
                response.usage.input_tokens,
                response.usage.output_tokens,
                model,
            )

            # Parse response
            raw_text = response.content[0].text
            parsed = _parse_response(raw_text)
            validated = _validate_response(parsed)

            return DirectionAssessment(
                direction=validated["direction"],
                confidence=validated["confidence"],
                reasoning=validated["reasoning"],
                key_directional_factors=validated["key_directional_factors"],
                claude_model_version=model,
                prompt_version=prompt_version,
                entity_id=entity_id,
                section_name=section_name,
                filing_date=filing_date,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        except json.JSONDecodeError as exc:
            # Malformed JSON — retrying won't help
            logger.warning(
                "Malformed JSON from Claude for %s/%s: %s",
                entity_id, section_name, exc,
            )
            return _neutral_assessment(entity_id, section_name, filing_date, model, prompt_version)

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
        "Claude API failed after %d attempts for %s/%s: %s",
        _MAX_RETRIES, entity_id, section_name, last_exc,
    )
    return _neutral_assessment(entity_id, section_name, filing_date, model, prompt_version)


def _neutral_assessment(
    entity_id: str,
    section_name: str,
    filing_date: str,
    model: str,
    prompt_version: str,
) -> DirectionAssessment:
    """Return a neutral assessment with zero confidence (fallback)."""
    return DirectionAssessment(
        direction="neutral",
        confidence=0.0,
        reasoning="Assessment unavailable — API call failed or was not attempted.",
        key_directional_factors=[],
        claude_model_version=model,
        prompt_version=prompt_version,
        entity_id=entity_id,
        section_name=section_name,
        filing_date=filing_date,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
