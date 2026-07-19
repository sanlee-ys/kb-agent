"""Assert this consumer still matches the provider's published /classify contract.

This is the **consumer half** of the SYS-004 guard. The provider
(`defense-news-classifier`) owns the wire contract and publishes it as a committed
artifact; this script fetches that artifact and fails if this repo's belief about
the response shape has drifted from it.

Why it exists. Both repos previously had "contract tests" that asserted each
implementation against its *own* copy of the shape. When the provider shipped a
third field (``region``) in v3.0.0, its fixture moved in the same commit and this
repo's stub did not — and both suites stayed green while the consumer was silently
out of contract. Unit tests cannot catch that by construction: nothing in this repo
could observe the provider. This script is the only thing here that looks outward.

What counts as a failure:

* **Divergence** (fetch succeeded, shapes differ) -> exit 1. This is the real guard.
* **Fetch failure** (network, DNS, timeout, non-200) -> exit 0 with a loud warning.
  A GitHub outage should not redden an unrelated build. The cost is that a genuine
  provider outage looks like a pass, which is why the warning is explicit rather
  than swallowed.

Run locally:
    uv run python scripts/check_classify_contract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _contract_fetch import fetch_contract, report  # noqa: E402

from agent.tools import CLASSIFY_REQUIRED_FIELDS  # noqa: E402

CONTRACT_URL = (
    "https://raw.githubusercontent.com/sanlee-ys/defense-news-classifier/"
    "main/contracts/classify-response.schema.json"
)


def compare(schema: dict) -> list[str]:
    """Compare this consumer's expected field set against the published contract.

    Returns:
        A list of human-readable problems; empty means the shapes agree.
    """
    problems: list[str] = []

    published = schema.get("required")
    if not isinstance(published, list):
        return ["The published contract has no 'required' list — cannot compare."]

    ours = list(CLASSIFY_REQUIRED_FIELDS)
    if published != ours:
        missing_here = [f for f in published if f not in ours]
        extra_here = [f for f in ours if f not in published]
        problems.append(
            f"Field set drift.\n"
            f"  provider requires : {published}\n"
            f"  this consumer has : {ours}"
        )
        if missing_here:
            problems.append(
                f"  The provider now returns {missing_here}, which this repo does "
                f"not read. Add it to CLASSIFY_REQUIRED_FIELDS and surface it in "
                f"classify_snippet's payload."
            )
        if extra_here:
            problems.append(
                f"  This repo requires {extra_here}, which the provider no longer "
                f"returns. Every classify_snippet call will now fail as a contract "
                f"violation."
            )

    if schema.get("additionalProperties") is not False:
        problems.append(
            "The published contract is no longer closed "
            "(additionalProperties is not false), so an added provider field "
            "would stop being detectable."
        )

    return problems


def main() -> int:
    """Fetch the published contract and fail on divergence."""
    schema = fetch_contract(CONTRACT_URL)
    if schema is None:
        print("Contract check SKIPPED (see warning above).")
        return 0

    return report(
        seam="SYS-004 POST /classify",
        url=CONTRACT_URL,
        problems=compare(schema),
        ok_message=(
            f"Contract OK — consumer matches the published provider contract "
            f"{list(CLASSIFY_REQUIRED_FIELDS)}."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
