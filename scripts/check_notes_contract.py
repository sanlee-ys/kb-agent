"""Assert this consumer still matches notes-api's published GET /notes contract.

The SYS-006 half of the cross-repo guard, alongside
``check_classify_contract.py`` for SYS-004. `search_notes` reads notes from
notes-api over HTTP to ground the agent's answers; this checks that the fields it
reads still exist upstream.

**This seam's contract is open, and that changes what counts as drift.** notes-api
returns more fields than this consumer uses, so an ADDED provider field is
backward-compatible and is not a failure — flagging it would make every additive
provider change redden this build for nothing. What is a failure is one of the
fields in ``NOTES_READ_FIELDS`` disappearing.

That failure is worth guarding precisely because it is quiet: `search_notes`
parses with ``.get()``, so a removed field yields ``None`` rather than an error.
The agent keeps answering, with notes whose titles are missing, and nothing looks
broken until an answer is wrong.

Run locally:
    uv run python scripts/check_notes_contract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _contract_fetch import fetch_contract, report  # noqa: E402

from agent.tools import NOTES_READ_FIELDS  # noqa: E402

CONTRACT_URL = (
    "https://raw.githubusercontent.com/sanlee-ys/notes-api/"
    "main/contracts/notes-read.schema.json"
)


def compare(schema: dict) -> list[str]:
    """Check every field this consumer reads still exists in the contract.

    Returns:
        A list of human-readable problems; empty means the shapes agree.
    """
    problems: list[str] = []

    published = schema.get("required")
    if not isinstance(published, list):
        return ["The published contract has no 'required' list — cannot compare."]

    missing = [f for f in NOTES_READ_FIELDS if f not in published]
    if missing:
        problems.append(
            f"notes-api no longer returns {missing}, which search_notes reads.\n"
            f"  provider returns : {published}\n"
            f"  this consumer reads: {list(NOTES_READ_FIELDS)}\n"
            f"  This fails SILENTLY at runtime — .get() yields None, so the agent "
            f"keeps answering with incomplete notes. Update NOTES_READ_FIELDS and "
            f"the payload it builds."
        )

    # An added provider field is NOT drift here: this contract is open by design
    # and this consumer reads a subset. Saying so explicitly so a future reader
    # does not "fix" the omission.
    return problems


def main() -> int:
    """Fetch the published read contract and fail if a read field vanished."""
    schema = fetch_contract(CONTRACT_URL)
    if schema is None:
        print("Contract check SKIPPED (see warning above).")
        return 0

    return report(
        seam="SYS-006 GET /notes",
        url=CONTRACT_URL,
        problems=compare(schema),
        ok_message=(
            f"Contract OK — every field search_notes reads "
            f"{list(NOTES_READ_FIELDS)} is still published."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
