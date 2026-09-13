from __future__ import annotations

import json

import pytest

from limpet.coach.analyze import CoachError
from limpet.coach.focus import FOCUS_RECONCILIATION_SCHEMA, reconcile
from limpet.coach.schema import CoachingReport
from limpet.store import db
from tests.test_coach import VALID_REPORT, FakeAnthropicClient, _fake_response


@pytest.fixture
def conn(data_dir):
    with db.session() as c:
        yield c


def test_reconcile_with_nothing_tracked_opens_new_rows_without_calling_the_api(conn):
    class ExplodingClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("should not call the API with nothing to reconcile against")

    report = CoachingReport.model_validate(VALID_REPORT)
    reconcile(ExplodingClient(), conn, match_id=1, report=report, now=1000)

    rows = db.all_focus_areas(conn)
    assert len(rows) == 1
    assert rows[0]["theme"] == "Solo rotations"
    assert rows[0]["dimension"] == "macro"
    assert rows[0]["status"] == "active"
    assert json.loads(rows[0]["evidence_match_ids"]) == [1]


def test_reconcile_updates_existing_status_and_evidence(conn):
    focus_id = db.open_focus_area(
        conn, match_id=1, dimension="macro", theme="Solo rotations", now=1000
    )

    fake_result = {
        "updates": [{"focus_area_id": focus_id, "status": "improving"}],
        "new_focus_areas": [],
    }
    client = FakeAnthropicClient(_fake_response(text=json.dumps(fake_result)))
    report = CoachingReport.model_validate(VALID_REPORT)

    reconcile(client, conn, match_id=2, report=report, now=2000)

    row = db.all_focus_areas(conn)[0]
    assert row["status"] == "improving"
    assert row["last_seen_match"] == 2
    assert json.loads(row["evidence_match_ids"]) == [1, 2]
    # the real API call carried our custom schema
    assert (
        client.messages.last_call_kwargs["output_config"]["format"]["schema"]
        == FOCUS_RECONCILIATION_SCHEMA
    )


def test_reconcile_opens_genuinely_new_focus_areas(conn):
    db.open_focus_area(conn, match_id=1, dimension="macro", theme="Solo rotations", now=1000)
    fake_result = {
        "updates": [],
        "new_focus_areas": [{"dimension": "micro", "theme": "Missed easy kills"}],
    }
    client = FakeAnthropicClient(_fake_response(text=json.dumps(fake_result)))
    report = CoachingReport.model_validate(VALID_REPORT)

    reconcile(client, conn, match_id=2, report=report, now=2000)

    themes = {r["theme"] for r in db.all_focus_areas(conn)}
    assert "Missed easy kills" in themes


def test_reconcile_missing_update_for_a_tracked_row_leaves_it_untouched(conn):
    focus_id = db.open_focus_area(
        conn, match_id=1, dimension="macro", theme="Solo rotations", now=1000
    )
    client = FakeAnthropicClient(
        _fake_response(text=json.dumps({"updates": [], "new_focus_areas": []}))
    )
    report = CoachingReport.model_validate(VALID_REPORT)

    reconcile(client, conn, match_id=2, report=report, now=2000)

    row = db.all_focus_areas(conn)[0]
    assert row["id"] == focus_id
    assert row["status"] == "active"  # unchanged, not guessed at
    assert json.loads(row["evidence_match_ids"]) == [1]  # not touched


def test_reconcile_raises_coach_error_on_refusal(conn):
    from types import SimpleNamespace

    db.open_focus_area(conn, match_id=1, dimension="macro", theme="Solo rotations", now=1000)
    client = FakeAnthropicClient(
        _fake_response(stop_reason="refusal", stop_details=SimpleNamespace(category=None))
    )
    report = CoachingReport.model_validate(VALID_REPORT)

    with pytest.raises(CoachError, match="declined"):
        reconcile(client, conn, match_id=2, report=report, now=2000)
