"""The LLM call: briefing in, a validated `CoachingReport` out.

Takes an `anthropic.Anthropic` client rather than constructing one, so tests
can inject a fake — same dependency-injection shape as `DeadlockClient`
elsewhere in this codebase.

Two paths share `_validate_report()`: the live single-match call
(`analyze_match`) and the Message Batches path (`submit_batch`/`poll_batch`,
used by `limpet backfill`) — a batch result's `result.message` is the same
`Message` shape as a live response, so the refusal/text/schema handling is
identical either way.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from .prompt import SYSTEM_PROMPT
from .schema import REPORT_JSON_SCHEMA, CoachingReport

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16_000
BATCH_POLL_INTERVAL_S = 30


class CoachError(RuntimeError):
    """Anything that stopped a coaching report from being produced."""


def _system_block() -> list[dict[str, Any]]:
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _output_config() -> dict[str, Any]:
    return {"effort": "high", "format": {"type": "json_schema", "schema": REPORT_JSON_SCHEMA}}


def _validate_report(response: Any) -> CoachingReport:
    """Turn a `Message`-shaped response (live or from a batch result) into a
    validated `CoachingReport`, or raise `CoachError`."""
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise CoachError(f"Claude declined to analyze this match (category: {category})")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise CoachError(f"No text content in the response (stop_reason={response.stop_reason})")

    try:
        return CoachingReport.model_validate(json.loads(text))
    except Exception as e:
        raise CoachError(f"Coaching response didn't match the expected schema: {e}") from e


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
            system=_system_block(),
            thinking={"type": "adaptive"},
            output_config=_output_config(),
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

    return _validate_report(response), response.usage


def submit_batch(
    client: anthropic.Anthropic,
    briefings: dict[int, dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Submit one Message Batches request per `{match_id: briefing}` entry.

    `custom_id` is the match id (as a string — the Batches API requires a
    string id) so results can be keyed back to their match. Returns the
    batch id.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    try:
        batch = client.messages.batches.create(
            requests=[
                Request(
                    custom_id=str(match_id),
                    params=MessageCreateParamsNonStreaming(
                        model=model,
                        max_tokens=max_tokens,
                        system=_system_block(),
                        thinking={"type": "adaptive"},
                        output_config=_output_config(),
                        messages=[{"role": "user", "content": json.dumps(briefing)}],
                    ),
                )
                for match_id, briefing in briefings.items()
            ]
        )
    except anthropic.AuthenticationError as e:
        raise CoachError(
            "Anthropic authentication failed — check ANTHROPIC_API_KEY / LIMPET_ANTHROPIC_API_KEY."
        ) from e
    except anthropic.APIStatusError as e:
        raise CoachError(f"Anthropic API error ({e.status_code}) submitting the batch.") from e
    except anthropic.APIConnectionError as e:
        raise CoachError(f"Could not reach the Anthropic API to submit the batch: {e}") from e

    return batch.id


def poll_batch(
    client: anthropic.Anthropic,
    batch_id: str,
    *,
    interval_s: float = BATCH_POLL_INTERVAL_S,
    on_tick: Any = None,
) -> Any:
    """Block until the batch finishes processing. Calls `on_tick(batch)` (if
    given) after every status check, including the final one."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if on_tick is not None:
            on_tick(batch)
        if batch.processing_status == "ended":
            return batch
        time.sleep(interval_s)


def collect_batch_reports(
    client: anthropic.Anthropic, batch_id: str
) -> dict[int, CoachingReport | CoachError]:
    """Read every result of a finished batch, keyed by match id (never by
    position — the Batches API does not guarantee result order)."""
    out: dict[int, CoachingReport | CoachError] = {}
    for result in client.messages.batches.results(batch_id):
        match_id = int(result.custom_id)
        if result.result.type == "succeeded":
            try:
                out[match_id] = _validate_report(result.result.message)
            except CoachError as e:
                out[match_id] = e
        elif result.result.type == "errored":
            out[match_id] = CoachError(f"Batch request errored: {result.result.error}")
        else:
            out[match_id] = CoachError(f"Batch request {result.result.type}")
    return out
