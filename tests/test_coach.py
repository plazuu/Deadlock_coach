from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from limpet.coach.analyze import CoachError, analyze_match
from limpet.coach.briefing import build as build_briefing
from limpet.coach.briefing import estimate_tokens
from limpet.coach.schema import REPORT_JSON_SCHEMA, CoachingReport

VALID_REPORT = {
    "summary": "You farmed well but died alone twice before 10 minutes.",
    "micro": {
        "assessment": "Solid CS, accuracy dipped late.",
        "rating": 62,
        "strengths": ["Good last-hitting under pressure"],
        "mistakes": [
            {
                "timestamp_s": 620,
                "theme": "Missed easy kill window",
                "what_happened": "Whiffed the finishing shot on a low-health target.",
                "why_it_mattered": "Would have won the fight outright.",
                "fix": "Lead moving targets more with your primary.",
                "mental": False,
            }
        ],
    },
    "macro": {
        "assessment": "Got caught rotating without vision twice.",
        "rating": 48,
        "strengths": [],
        "mistakes": [
            {
                "timestamp_s": 340,
                "theme": "Isolated death",
                "what_happened": "Walked into enemy jungle alone at 5:40.",
                "why_it_mattered": "Gave up a lane advantage and tempo.",
                "fix": "Check the minimap before crossing the river solo.",
                "mental": False,
            }
        ],
    },
    "focus_this_week": [
        {
            "dimension": "macro",
            "theme": "Solo rotations",
            "why": "Two of your deaths came from rotating alone.",
            "drill": "Ping/wait for a teammate before crossing map thirds.",
        }
    ],
    "progress_note": "First analyzed match — no prior focus areas to compare against.",
}


def _fake_response(*, stop_reason="end_turn", text=None, stop_details=None):
    content = []
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=content,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_call_kwargs = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return self._response


class FakeAnthropicClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def test_coaching_report_schema_round_trips():
    report = CoachingReport.model_validate(VALID_REPORT)
    assert report.micro.rating == 62
    assert report.focus_this_week[0].dimension == "macro"


def test_report_json_schema_is_well_formed():
    assert REPORT_JSON_SCHEMA["type"] == "object"
    assert REPORT_JSON_SCHEMA["additionalProperties"] is False
    assert set(REPORT_JSON_SCHEMA["required"]) == {
        "summary",
        "micro",
        "macro",
        "focus_this_week",
        "progress_note",
    }


def test_build_briefing_shape():
    briefing = build_briefing(
        {"micro": {}, "macro": {}},
        match_id=123,
        hero_name="Vindicta",
        won=True,
        duration_s=1800,
        rank_name="Archon 3",
        active_focus_areas=[{"dimension": "micro", "theme": "aim", "status": "active"}],
    )
    assert briefing["result"] == "win"
    assert briefing["duration_min"] == 30.0
    assert briefing["active_focus_areas"][0]["theme"] == "aim"
    assert "item_builds" not in briefing


def test_build_briefing_includes_item_builds_when_given():
    briefing = build_briefing(
        {"micro": {}, "macro": {}},
        match_id=123,
        hero_name="Vindicta",
        won=True,
        duration_s=1800,
        rank_name="Archon 3",
        item_builds={"our_build": [], "enemies": []},
    )
    assert briefing["item_builds"] == {"our_build": [], "enemies": []}


def test_estimate_tokens_is_roughly_length_over_four():
    assert estimate_tokens("a" * 400) == 100


def test_analyze_match_returns_validated_report():
    client = FakeAnthropicClient(_fake_response(text=json.dumps(VALID_REPORT)))
    report, usage = analyze_match(client, {"hero": "Vindicta"})
    assert isinstance(report, CoachingReport)
    assert usage.input_tokens == 100
    # system prompt + schema + briefing were actually sent
    kwargs = client.messages.last_call_kwargs
    assert kwargs["output_config"]["format"]["schema"] == REPORT_JSON_SCHEMA
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_analyze_match_raises_on_refusal():
    resp = _fake_response(
        stop_reason="refusal", stop_details=SimpleNamespace(category="frontier_llm")
    )
    client = FakeAnthropicClient(resp)
    with pytest.raises(CoachError, match="declined"):
        analyze_match(client, {})


def test_analyze_match_raises_on_missing_text_block():
    client = FakeAnthropicClient(_fake_response(text=None))
    with pytest.raises(CoachError, match="No text content"):
        analyze_match(client, {})


def test_analyze_match_raises_on_schema_mismatch():
    client = FakeAnthropicClient(_fake_response(text=json.dumps({"not": "a report"})))
    with pytest.raises(CoachError, match="didn't match"):
        analyze_match(client, {})
