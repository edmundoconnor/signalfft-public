"""Claude-based quiet filing triage — API call, prompt, response parsing.

Follows the same pattern as engine.directional.claude_interpreter:
- Loads prompt template from YAML
- Truncates text to ~8K tokens
- Calls Claude Sonnet for cost efficiency
- Parses and validates JSON response
- Logs cost per API call
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
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
_MAX_INPUT_CHARS = 32_000  # ~8000 tokens
_API_TIMEOUT = 30
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0

# Sonnet pricing (USD per million tokens)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0

_TRUNCATION_MARKER = "\n\n[... middle section truncated for length ...]\n\n"

# Prompt versions for caching
PROMPT_VERSION = "1.0"


# ---------------------------------------------------------------------------
# TriageAssessment dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TriageAssessment:
    """Result of Claude quiet filing triage."""

    materiality_score: int  # 1-10
    attention_likelihood: str  # "low", "medium", "high"
    direction: str  # "bullish", "bearish", "neutral"
    reasoning: str
    key_material_items: list[str]
    suggested_urgency: str  # "monitor", "investigate", "act"
    is_quiet_filing: bool
    boost_multiplier: float
    claude_model_version: str
    prompt_version: str
    entity_id: str
    form_type: str
    filing_date: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    created_at: str = ""


# ---------------------------------------------------------------------------
# Prompt loading and versioning
# ---------------------------------------------------------------------------

_prompt_cache: dict[str, Any] | None = None
_prompt_version_cache: str | None = None


def _resolve_prompt_path() -> Path:
    """Resolve path to the quiet filing triage prompt YAML."""
    env_path = os.environ.get("TRIAGE_PROMPT_PATH", "")
    if env_path:
        return Path(env_path)
    engine_root = Path(__file__).resolve().parents[4]  # signalfft-engine/
    return engine_root.parent / "signalfft-opus" / "prompts" / "quiet_filing_triage.yaml"


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
    """Clear the prompt cache (for testing)."""
    global _prompt_cache, _prompt_version_cache
    _prompt_cache = None
    _prompt_version_cache = None


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

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
    filing_text: str,
    entity_id: str,
    form_type: str,
    filing_date: str,
    context: dict[str, Any],
    tier1_keywords: list[dict] | None = None,
) -> tuple[str, str]:
    """Build system and user prompts from the YAML template.

    Returns (system_prompt, user_prompt).
    """
    template = _load_prompt_template()
    system_prompt = template["system"].strip()

    truncated = truncate_text(filing_text)

    keywords_str = "None"
    if tier1_keywords:
        kw_list = [t.get("term", "") for t in tier1_keywords]
        keywords_str = ", ".join(kw_list) if kw_list else "None"

    user_prompt = template["user_template"].format(
        filing_text=truncated,
        entity_id=entity_id,
        form_type=form_type,
        filing_date=filing_date,
        filing_time_context=context.get("filing_time_context", "unknown"),
        is_after_hours=context.get("is_after_hours", False),
        is_friday=context.get("is_friday", False),
        is_holiday_adjacent=context.get("is_holiday_adjacent", False),
        is_amended=context.get("is_amended", False),
        has_press_release=context.get("has_press_release", False),
        tier1_keywords_matched=keywords_str,
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


def validate_response(data: dict) -> dict:
    """Validate and normalize parsed triage response."""
    materiality = data.get("materiality_score", 1)
    if not isinstance(materiality, int):
        try:
            materiality = int(materiality)
        except (ValueError, TypeError):
            materiality = 1
    materiality = max(1, min(10, materiality))

    attention = data.get("attention_likelihood", "medium")
    if attention not in ("low", "medium", "high"):
        attention = "medium"

    direction = data.get("direction", "neutral")
    if direction not in ("bullish", "bearish", "neutral"):
        direction = "neutral"

    reasoning = str(data.get("reasoning", ""))

    items = data.get("key_material_items", [])
    if not isinstance(items, list):
        items = []
    items = [str(i) for i in items[:5]]

    urgency = data.get("suggested_urgency", "monitor")
    if urgency not in ("monitor", "investigate", "act"):
        urgency = "monitor"

    return {
        "materiality_score": materiality,
        "attention_likelihood": attention,
        "direction": direction,
        "reasoning": reasoning,
        "key_material_items": items,
        "suggested_urgency": urgency,
    }


# ---------------------------------------------------------------------------
# Quiet filing detection + boost
# ---------------------------------------------------------------------------

_DEFAULT_BOOST_MULTIPLIER = 1.5


def compute_boost(
    materiality_score: int,
    attention_likelihood: str,
    boost_multiplier: float = _DEFAULT_BOOST_MULTIPLIER,
) -> tuple[bool, float]:
    """Determine if this is a quiet filing and what boost to apply.

    Returns (is_quiet_filing, effective_multiplier).
    """
    if materiality_score >= 7 and attention_likelihood == "low":
        return True, boost_multiplier
    return False, 1.0


# ---------------------------------------------------------------------------
# Cost logging
# ---------------------------------------------------------------------------

def log_cost(
    entity_id: str,
    form_type: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float:
    """Log API call cost and return estimated cost."""
    input_cost = (input_tokens * _INPUT_PRICE_PER_M) / 1_000_000
    output_cost = (output_tokens * _OUTPUT_PRICE_PER_M) / 1_000_000
    total_cost = input_cost + output_cost

    logger.info(
        "Triage API cost: entity=%s form_type=%s model=%s "
        "input_tokens=%d output_tokens=%d estimated_cost=$%.6f",
        entity_id, form_type, model,
        input_tokens, output_tokens, total_cost,
    )
    return total_cost


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def call_triage(
    filing_text: str,
    entity_id: str,
    form_type: str,
    filing_date: str,
    context: dict[str, Any],
    tier1_keywords: list[dict] | None = None,
    boost_multiplier: float = _DEFAULT_BOOST_MULTIPLIER,
) -> TriageAssessment:
    """Call Claude to triage a filing section.

    Returns TriageAssessment. On failure, returns safe defaults
    (materiality_score=1, attention_likelihood="medium").
    """
    import asyncio

    model = os.environ.get("CLAUDE_MODEL_ID", _DEFAULT_MODEL)
    system_prompt, user_prompt = build_prompt(
        filing_text, entity_id, form_type, filing_date, context, tier1_keywords,
    )
    prompt_version = get_prompt_version()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning default triage assessment")
        return _default_assessment(
            entity_id, form_type, filing_date, model, prompt_version,
        )

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
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            estimated_cost = log_cost(
                entity_id, form_type, input_tokens, output_tokens, model,
            )

            raw_text = response.content[0].text
            parsed = parse_response(raw_text)
            validated = validate_response(parsed)

            is_quiet, effective_mult = compute_boost(
                validated["materiality_score"],
                validated["attention_likelihood"],
                boost_multiplier,
            )

            return TriageAssessment(
                materiality_score=validated["materiality_score"],
                attention_likelihood=validated["attention_likelihood"],
                direction=validated["direction"],
                reasoning=validated["reasoning"],
                key_material_items=validated["key_material_items"],
                suggested_urgency=validated["suggested_urgency"],
                is_quiet_filing=is_quiet,
                boost_multiplier=effective_mult,
                claude_model_version=model,
                prompt_version=prompt_version,
                entity_id=entity_id,
                form_type=form_type,
                filing_date=filing_date,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        except json.JSONDecodeError as exc:
            logger.warning(
                "Malformed JSON from Claude triage for %s/%s: %s",
                entity_id, form_type, exc,
            )
            return _default_assessment(
                entity_id, form_type, filing_date, model, prompt_version,
            )

        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Triage API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                await asyncio.sleep(wait)

    logger.error(
        "Triage API failed after %d attempts for %s/%s: %s",
        _MAX_RETRIES, entity_id, form_type, last_exc,
    )
    return _default_assessment(
        entity_id, form_type, filing_date, model, prompt_version,
    )


def _default_assessment(
    entity_id: str,
    form_type: str,
    filing_date: str,
    model: str,
    prompt_version: str,
) -> TriageAssessment:
    """Return safe default assessment (fallback)."""
    return TriageAssessment(
        materiality_score=1,
        attention_likelihood="medium",
        direction="neutral",
        reasoning="Assessment unavailable — API call failed or was not attempted.",
        key_material_items=[],
        suggested_urgency="monitor",
        is_quiet_filing=False,
        boost_multiplier=1.0,
        claude_model_version=model,
        prompt_version=prompt_version,
        entity_id=entity_id,
        form_type=form_type,
        filing_date=filing_date,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
