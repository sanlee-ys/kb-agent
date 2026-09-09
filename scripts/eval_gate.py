"""Grade two retrieval-eval JSON files against evals/thresholds.toml.

Pure and offline: this module never calls the network, never opens ChromaDB,
and never writes a baseline. It reads JSON that ``scripts/eval_retrieval.py
--json`` already wrote, and it grades ``summary.overall`` against the measured
floors in ``evals/thresholds.toml``.

CI re-measures every PR (the retrieval eval is free and deterministic) and
then runs this script. A non-zero exit means retrieval got worse or the
harness broke. A snapshot whose ``n`` is not the gold-set size is a fail,
not a grade over a smaller sample (SYS-017 corollary: a gate that cannot
fail is theater).

    uv run python scripts/eval_retrieval.py --json eval/ci_unfiltered.json
    uv run python scripts/eval_retrieval.py --kind-filter --json eval/ci_kind_filter.json
    uv run python scripts/eval_gate.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLDS = REPO_ROOT / "evals" / "thresholds.toml"
DEFAULT_UNFILTERED = REPO_ROOT / "eval" / "ci_unfiltered.json"
DEFAULT_KIND_FILTER = REPO_ROOT / "eval" / "ci_kind_filter.json"

ARMS = ("unfiltered", "kind_filter")
GATED_METRICS = ("recall@1", "recall@5", "mrr")


class GateError(Exception):
    """Raised when the gate cannot grade a run (missing or malformed input)."""


def load_thresholds(path: Path = DEFAULT_THRESHOLDS) -> dict:
    """Load the floor tables the gate grades against.

    Args:
        path: Path to the thresholds TOML file.

    Returns:
        Parsed TOML with ``unfiltered`` and ``kind_filter`` tables.

    Raises:
        GateError: The file is missing, unreadable, or lacks a required table.
    """
    if not path.is_file():
        raise GateError(f"missing floors file: {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"malformed floors file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"malformed floors file {path}: expected a table")
    for arm in ARMS:
        table = data.get(arm)
        if not isinstance(table, dict):
            raise GateError(f"floors file {path} is missing the [{arm}] table")
        if "n" not in table:
            raise GateError(f"floors file {path} [{arm}] is missing n")
        for key in GATED_METRICS:
            if key not in table:
                raise GateError(f"floors file {path} [{arm}] is missing {key!r}")
    return data


def load_run(path: Path) -> dict:
    """Read ``summary.overall`` from an eval_retrieval.py --json file.

    Args:
        path: Path to the JSON file.

    Returns:
        The ``summary.overall`` dict (``n``, ``recall@1``, ``recall@5``, ``mrr``).

    Raises:
        GateError: The file is missing, not JSON, or lacks ``summary.overall``.
    """
    if not path.is_file():
        raise GateError(f"missing JSON file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateError(f"malformed JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"malformed JSON in {path}: expected an object")
    summary = data.get("summary")
    if not isinstance(summary, dict):
        raise GateError(f"malformed JSON in {path}: missing summary object")
    overall = summary.get("overall")
    if not isinstance(overall, dict):
        raise GateError(f"malformed JSON in {path}: missing summary.overall")
    return overall


def grade_arm(arm: str, overall: dict, floors: dict) -> list[str]:
    """Grade one arm's overall slice against its floor table.

    Refuses to grade metrics when ``n`` does not match the floors file. A
    truncated snapshot that still clears every floor is a fail.

    Args:
        arm: Arm name (``unfiltered`` or ``kind_filter``), for error text.
        overall: ``summary.overall`` from a saved run.
        floors: The matching table from ``evals/thresholds.toml``.

    Returns:
        A list of problem strings. Empty means the arm cleared every floor.
    """
    problems: list[str] = []
    expected_n = floors["n"]
    actual_n = overall.get("n")
    if actual_n != expected_n:
        problems.append(
            f"{arm}: refuse to grade: n={actual_n!r} != {expected_n} "
            "(partial snapshot is a fail, SYS-017 corollary)"
        )
        return problems

    for key in GATED_METRICS:
        if key not in overall:
            problems.append(f"{arm}: missing metric {key!r} in summary.overall")
            continue
        value = overall[key]
        floor = floors[key]
        try:
            value_num = float(value)
            floor_num = float(floor)
        except (TypeError, ValueError):
            problems.append(f"{arm}: {key} is not numeric (value={value!r}, floor={floor!r})")
            continue
        if value_num < floor_num:
            problems.append(f"{arm}: {key} {value_num:.3f} is below floor {floor_num:.2f}")
    return problems


def _print_report(
    unfiltered: dict,
    kind_filter: dict,
    floors: dict,
    problems: list[str],
) -> None:
    """Print a compact arm/metric/value/floor table, then any problems."""
    print("arm          metric     value    floor  result")
    for arm, overall in (("unfiltered", unfiltered), ("kind_filter", kind_filter)):
        table = floors[arm]
        n_ok = overall.get("n") == table["n"]
        n_result = "PASS" if n_ok else "FAIL"
        print(f"{arm:<12} {'n':<10} {overall.get('n')!s:<8} {table['n']!s:<6} {n_result}")
        if not n_ok:
            continue
        for key in GATED_METRICS:
            value = overall.get(key)
            floor = table[key]
            try:
                passed = float(value) >= float(floor)
                value_s = f"{float(value):.3f}"
                floor_s = f"{float(floor):.2f}"
            except (TypeError, ValueError):
                passed = False
                value_s = str(value)
                floor_s = str(floor)
            result = "PASS" if passed else "FAIL"
            print(f"{arm:<12} {key:<10} {value_s:<8} {floor_s:<6} {result}")
    if problems:
        print("\nFAIL")
        for problem in problems:
            print(f"  {problem}")
    else:
        print("\nOK - both retrieval arms clear the SYS-017 tier 2 floors.")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on pass, 1 on any problem.

    Args:
        argv: Optional argument list (for tests). ``None`` reads ``sys.argv``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--unfiltered",
        type=Path,
        default=DEFAULT_UNFILTERED,
        help="JSON from eval_retrieval.py --json (unfiltered arm)",
    )
    parser.add_argument(
        "--kind-filter",
        dest="kind_filter",
        type=Path,
        default=DEFAULT_KIND_FILTER,
        help="JSON from eval_retrieval.py --kind-filter --json",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=DEFAULT_THRESHOLDS,
        help="floors TOML (default: evals/thresholds.toml)",
    )
    args = parser.parse_args(argv)

    try:
        floors = load_thresholds(args.thresholds)
        unfiltered = load_run(args.unfiltered)
        kind_filter = load_run(args.kind_filter)
    except GateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    problems.extend(grade_arm("unfiltered", unfiltered, floors["unfiltered"]))
    problems.extend(grade_arm("kind_filter", kind_filter, floors["kind_filter"]))
    _print_report(unfiltered, kind_filter, floors, problems)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
