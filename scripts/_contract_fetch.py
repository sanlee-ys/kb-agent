"""Shared fetch for the cross-repo contract checks (SYS-018).

Both seam checks — `/classify` (provider: defense-news-classifier) and
`GET /notes` (provider: notes-api) — need the same thing: pull a published
schema artifact over HTTP and distinguish "the provider changed" from "GitHub is
having a bad day". Keeping that in one place rather than copied into each script
is not tidiness; two copies of one behaviour drifting apart unnoticed is the
exact failure SYS-018 exists to prevent, and it would be a poor look for the
scripts enforcing it.

The failure policy, applied identically to both seams:

* **Divergence** (fetch succeeded, shapes differ) -> the caller fails. Real guard.
* **Fetch failure** (network, DNS, timeout, non-200) -> warn and pass. A GitHub
  outage must not redden an unrelated build. The cost is that a genuine outage
  reads as a pass, so the warning is loud rather than swallowed.
* **404** -> warn that the check is INERT. This is what lets a consumer check
  merge before its provider has published, and self-arm the moment it does.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 15


def fetch_contract(url: str) -> dict | None:
    """Fetch a published contract artifact, or None if it is unreachable.

    Args:
        url: Raw URL of the provider's committed schema artifact.

    Returns:
        The parsed schema, or ``None`` when it could not be fetched or parsed.
        Callers treat ``None`` as a warning, never as a failure.
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(
                f"WARNING: the provider has not published this contract yet.\n"
                f"         {url}\n"
                f"         This check is INERT until it does — it is not a guard "
                f"right now.",
                file=sys.stderr,
            )
        else:
            print(f"WARNING: HTTP {exc.code} fetching {url}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"WARNING: could not fetch or parse {url}: {exc}", file=sys.stderr)
        return None


def report(seam: str, url: str, problems: list[str], ok_message: str) -> int:
    """Print a verdict and return the process exit code.

    Args:
        seam: The SYS number and endpoint, for the failure header.
        url: The published contract's URL, echoed so a reader can go look.
        problems: Human-readable divergences; empty means agreement.
        ok_message: What to print when the shapes agree.

    Returns:
        1 if there were problems, else 0.
    """
    if problems:
        print(f"{seam} CONTRACT DRIFT — this consumer no longer matches the provider:")
        for problem in problems:
            print(f"\n{problem}")
        print(
            f"\nPublished contract: {url}\n"
            "This is the failure mode SYS-018 exists to make loud. Do not silence "
            "this check; update the consumer."
        )
        return 1

    print(ok_message)
    return 0
