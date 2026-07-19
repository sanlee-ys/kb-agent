"""Offline tests for the cross-repo /classify contract check.

The check itself (``scripts/check_classify_contract.py``) is the one thing in this
repo that looks outward at the provider. These tests cover its *comparison logic*
offline — they deliberately do not hit the network, so the suite stays runnable
without it. The live fetch is exercised by the CI step, not here.

The cases below are the drift scenarios that actually matter, written from the one
that already happened: the provider adds a field and this consumer keeps reading
the old shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_classify_contract import compare  # noqa: E402

from agent.tools import CLASSIFY_REQUIRED_FIELDS  # noqa: E402


def _schema(required: list[str], closed: bool = True) -> dict:
    return {
        "required": required,
        "additionalProperties": not closed,
        "properties": {name: {"type": "string"} for name in required},
    }


def test_matching_contract_reports_no_problems():
    assert compare(_schema(list(CLASSIFY_REQUIRED_FIELDS))) == []


def test_provider_adding_a_field_is_drift():
    """The exact failure that went undetected on 2026-07-18."""
    problems = compare(_schema([*CLASSIFY_REQUIRED_FIELDS, "confidence"]))
    assert problems
    joined = " ".join(problems)
    assert "confidence" in joined
    # The message must tell the reader what to do, not just that something broke.
    assert "CLASSIFY_REQUIRED_FIELDS" in joined


def test_provider_removing_a_field_is_drift():
    """The mirror case: every classify_snippet call would fail as a violation."""
    problems = compare(_schema(["category", "operational_domain"]))
    assert problems
    assert "region" in " ".join(problems)


def test_reordered_fields_are_drift():
    """Order is part of the published contract, so a reorder is worth surfacing.

    Deliberately strict: a reorder is cheap to fix and a silent reorder means the
    two lists are being maintained independently again.
    """
    reordered = ["region", "category", "operational_domain"]
    assert compare(_schema(reordered))


def test_an_opened_contract_is_flagged():
    """`additionalProperties: false` is what makes an added field detectable."""
    problems = compare(_schema(list(CLASSIFY_REQUIRED_FIELDS), closed=False))
    assert any("no longer closed" in p for p in problems)


def test_a_contract_with_no_required_list_is_flagged():
    problems = compare({"additionalProperties": False})
    assert problems
    assert "cannot compare" in problems[0]
