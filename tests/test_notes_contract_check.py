"""Offline tests for the cross-repo GET /notes contract check (SYS-006).

The companion to ``test_classify_contract_check.py``. Same shape, deliberately
different semantics — and the difference is the point worth testing.

`/classify` is a **closed** contract: this consumer must read every field, so a
provider adding one is drift. `GET /notes` is **open**: notes-api returns eight
fields and `search_notes` reads four, so an added provider field is
backward-compatible and must NOT fail the build. Flagging it would make every
additive provider change redden CI for nothing, and a check that cries wolf gets
silenced.

What must fail is a field this consumer reads disappearing — which at runtime is
quiet, since `.get()` yields ``None`` and the agent keeps answering with
incomplete notes.

No network: the live fetch is exercised by the CI step.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_notes_contract import compare  # noqa: E402

from agent.tools import NOTES_READ_FIELDS  # noqa: E402

# What notes-api actually publishes today, verified against its NoteResponse.
PUBLISHED = [
    "id",
    "title",
    "content",
    "tags",
    "enrichment_status",
    "published_at",
    "created_at",
    "updated_at",
]


def _schema(required: list[str]) -> dict:
    return {"required": required, "additionalProperties": True}


def test_current_shape_reports_no_problems():
    assert compare(_schema(PUBLISHED)) == []


def test_consumer_reads_a_strict_subset():
    """Guards the premise the open-contract semantics rest on."""
    assert set(NOTES_READ_FIELDS) < set(PUBLISHED)


def test_added_provider_field_is_not_drift():
    """The asymmetry with /classify, pinned.

    A future "make it consistent with the classify check" edit would break every
    additive provider change. This test is why not to.
    """
    assert compare(_schema([*PUBLISHED, "summary"])) == []


def test_removing_a_field_the_consumer_reads_is_drift():
    without_title = [f for f in PUBLISHED if f != "title"]
    problems = compare(_schema(without_title))
    assert problems
    assert "title" in problems[0]


def test_drift_message_names_the_silent_failure():
    """The message must explain WHY this matters, not just that it happened.

    A removed field does not crash anything — it yields None and the agent keeps
    answering. A reader who does not know that will not treat it as urgent.
    """
    problems = compare(_schema([f for f in PUBLISHED if f != "content"]))
    assert "SILENTLY" in problems[0]
    assert "NOTES_READ_FIELDS" in problems[0]


def test_removing_a_field_the_consumer_ignores_is_not_drift():
    """`created_at` is published but unread; losing it is the provider's business."""
    assert compare(_schema([f for f in PUBLISHED if f != "created_at"])) == []


def test_contract_with_no_required_list_is_flagged():
    problems = compare({"additionalProperties": True})
    assert problems
    assert "cannot compare" in problems[0]
