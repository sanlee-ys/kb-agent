"""Lint this repo's ADRs for the 'Downstream surfaces' section.

Why this exists, concretely: in the classifier repo, `ADR-014` shipped the `region`
axis on 2026-07-18 with no `## Downstream surfaces` section. The sweep that followed
updated two eval modules and missed a third file that had not been touched in a week
and knew nothing about a new axis, leaving an automated loop able to silently delete
the new rubric. Nobody noticed for a day.

The `architecture` repo has enforced this section on its `SYS-NNN` documents since
2026-07-18, and the classifier ported it to its own ADRs on 2026-07-19. This repo's
`decisions/` tier had no check at all — which is the same gap one level down, and the
reason a decision here could ship without anyone being asked what it touches.

Deliberately narrow. It does NOT try to verify that the listed surfaces are correct or
complete — a linter cannot know that. It only enforces that the author was made to
think about it and write the list down, which is the step that gets skipped.

RATCHETED: documents in ``LEGACY_NO_DOWNSTREAM`` are grandfathered and the list may
only shrink. **It is empty here** — every ADR in this repo already carries the section,
so the rule is enforced with no exceptions and any new ADR must comply.

Usage:
    uv run python scripts/lint_decisions.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISIONS = REPO_ROOT / "decisions"

REQUIRED_SECTION = "## Downstream surfaces"

# Empty on purpose, and that is the strongest state this list can be in: every ADR
# here already complies. THIS LIST MAY ONLY SHRINK — adding an entry weakens the rule
# rather than fixing the document, which is the whole point of a ratchet.
LEGACY_NO_DOWNSTREAM: set[str] = set()

STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.MULTILINE)

# Filenames vary across the house: this repo uses `ADR-NNN-slug.md`, the classifier
# uses a bare `NNN-slug.md`. The *identifier* is always ADR-NNN, so match both rather
# than making the convention a portability problem.
_ADR_NAME = re.compile(r"^(?:ADR-)?(\d{3})-")


def adr_files() -> list[Path]:
    """Every numbered ADR, sorted. Excludes README.md and templates."""
    return sorted(p for p in DECISIONS.glob("*.md") if _ADR_NAME.match(p.name))


def adr_id(path: Path) -> str:
    """'002' from either 'ADR-002-slug.md' or '002-slug.md'."""
    match = _ADR_NAME.match(path.name)
    assert match is not None  # adr_files() only yields matching names
    return match.group(1)


def lint() -> list[str]:
    """Run the checks. Returns human-readable problems, empty if clean."""
    problems: list[str] = []
    seen: set[str] = set()

    for path in adr_files():
        num = adr_id(path)
        rel = f"decisions/{path.name}"
        text = path.read_text(encoding="utf-8")

        if num in seen:
            problems.append(f"{rel}: duplicate ADR number {num}.")
        seen.add(num)

        if not STATUS.search(text):
            problems.append(f"{rel}: no '**Status:**' header.")

        if REQUIRED_SECTION not in text and num not in LEGACY_NO_DOWNSTREAM:
            problems.append(
                f"{rel}: missing '{REQUIRED_SECTION}'. Every ADR must list the code, "
                f"docs, and other decisions its change touches ('None' is a valid "
                f"answer, but it has to be written). This is the step that gets "
                f"skipped, and the sweep that follows then misses a file."
            )

    # An entry naming an ADR that does not exist means someone edited the list
    # instead of the document.
    for num in sorted(LEGACY_NO_DOWNSTREAM - seen):
        problems.append(
            f"LEGACY_NO_DOWNSTREAM names {num}, but no such ADR exists. "
            f"The list may only shrink, and only by an ADR gaining a real section."
        )

    # An entry that now HAS the section should leave the list, or the ratchet
    # silently stops ratcheting.
    for path in adr_files():
        num = adr_id(path)
        if num in LEGACY_NO_DOWNSTREAM:
            if REQUIRED_SECTION in path.read_text(encoding="utf-8"):
                problems.append(
                    f"decisions/{path.name} has '{REQUIRED_SECTION}' but is still "
                    f"grandfathered in LEGACY_NO_DOWNSTREAM. Remove {num} from the "
                    f"list so the ratchet keeps its meaning."
                )

    return problems


def main() -> int:
    """Lint the decisions; exit 1 on any problem."""
    problems = lint()
    if problems:
        # ASCII only: this runs on a Windows console (cp1252) as well as in CI.
        print("DECISION LINT - problems found:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}\n", file=sys.stderr)
        return 1

    total = len(adr_files())
    grandfathered = len(LEGACY_NO_DOWNSTREAM)
    print(
        f"OK - decisions clean. {total} ADRs, {grandfathered} grandfathered out of "
        f"the '{REQUIRED_SECTION}' rule, {total - grandfathered} enforced."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
