"""The LLM call: briefing in, a validated `CoachingReport` out.

Takes an `anthropic.Anthropic` client rather than constructing one, so tests
can inject a fake — same dependency-injection shape as `DeadlockClient`
elsewhere in this codebase.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from .prompt import SYSTEM_PROMPT
from .schema import REPORT_JSON_SCHEMA, CoachingReport

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16_000


class CoachError(RuntimeError):
    """Anything that stopped a coaching report from being produced."""


def analyze_match(
    client: anthropic.Anthropic,
    briefing: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[CoachingReport, Any]:
    """Call Claude with the match briefing. Returns `(report, response.usage)`."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": REPORT_JSON_SCHEMA},
            },
            messages=[{"role": "user", "content": json.dumps(briefing)}],
        )
    except anthropic.AuthenticationError as e:
        raise CoachError(
            "Anthropic authentication failed — check ANTHROPIC_API_KEY / LIMPET_ANTHROPIC_API_KEY."
        ) from e
    except anthropic.RateLimitError as e:
        raise CoachError(f"Anthropic rate limit hit: {e}") from e
    except anthropic.APIStatusError as e:
        raise CoachError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise CoachError(f"Could not reach the Anthropic API: {e}") from e

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise CoachError(f"Claude declined to analyze this match (category: {category})")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise CoachError(f"No text content in the response (stop_reason={response.stop_reason})")

    try:
        report = CoachingReport.model_validate(json.loads(text))
    except Exception as e:
        raise CoachError(f"Coaching response didn't match the expected schema: {e}") from e

    return report, response.usage
