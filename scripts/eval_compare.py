"""Paired A/B comparison of two saved retrieval-eval runs.

Additive layer over scripts/eval_retrieval.py: run the harness twice — once on
the baseline retriever, once on a candidate — saving each run with ``--json``,
then compare the two files here. The existing eval path and its recorded
numbers are untouched; this script only reads what ``--json`` already writes.

    uv run python scripts/eval_retrieval.py --json eval/baseline.json
    # ...change the retriever...
    uv run python scripts/eval_retrieval.py --json eval/candidate.json
    uv run python scripts/eval_compare.py --baseline eval/baseline.json \
        --candidate eval/candidate.json

The comparison core is scripts/paired_compare.py (vendored from
defense-news-classifier, provenance in its docstring). This module is only the
adapter: it maps each per-query result to a core ``Observation``.

The unit of observation is a query with a graded retrieval result, not a
single label. Correctness is a thresholded hit: pass = an expected source in
the top ``--k`` (default 5, the harness's own cutoff). recall@1/@3/@5 and MRR
flow through the core's paired-mean path as per-query indicators, so their
deltas are computed over the same shared pair set as the pass rate.

Group keys: gold-set queries carry no natural id the *runs* can be trusted to
agree on — the ``id`` is a hand-assigned label whose underlying query text or
expected sources can change between eval-set revisions. So pairing keys on the
SHA-256 of the canonicalized identity content instead: the query id plus its
expected sources (plus the query text and any eval-set version field, when a
run records them). Two runs against different revisions of a query then show
up honestly as ``missing-observation`` on both sides, not as a false pair.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # scripts/ runs from anywhere; sibling import needs the root

from scripts.eval_retrieval import K_VALUES  # noqa: E402
from scripts.paired_compare import (  # noqa: E402
    Observation,
    Outcome,
    build_report,
    derive_group_key,
    pair_observations,
    summarize_correctness,
    summarize_metric,
)

# Identity fields a run may record for one query, in the order they matter.
# "id" is renamed so derive_group_key content-hashes instead of short-circuiting
# on the bare label (see the module docstring for why the label alone is not
# enough identity).
_IDENTITY_FIELDS = (
    ("id", "query_id"),
    ("query", "query"),
    ("eval_set_version", "eval_set_version"),
)


def group_key_for(result: dict) -> str:
    """Content-hash group key for one per-query result.

    Args:
        result: One entry of a saved run's ``results`` list.

    Returns:
        Hex SHA-256 of the canonicalized identity: query id + sorted expected
        sources, plus query text / eval-set version when the run recorded them.
    """
    identity: dict = {"expected": sorted(result.get("expected", []))}
    for source_field, target_field in _IDENTITY_FIELDS:
        if source_field in result:
            identity[target_field] = result[source_field]
    return derive_group_key(identity)


def observations_from_results(results: list[dict], arm: str, k: int) -> list[Observation]:
    """Map one run's per-query results to core observations.

    Outcome rules: a result with no ``rank`` field at all is ``unscored`` (the
    harness recorded the query but no graded outcome — distinct from a miss);
    otherwise it is ``scored``, 1.0 when the first hit is within the top ``k``
    and 0.0 otherwise (``rank: null`` is a genuine miss, not a harness error —
    eval_retrieval already aborts loudly on an unindexed KB). Per-query
    recall@K indicators and the reciprocal rank ride along as metrics.

    Args:
        results: The ``results`` list of one saved run.
        arm: Name of this arm, used in the report and diagnostics.
        k: Hit cutoff for the pass/fail score.

    Returns:
        One ``Observation`` per result, in file order. Duplicate queries are
        preserved rather than deduplicated — pairing is what flags them.
    """
    observations: list[Observation] = []
    for result in results:
        group_key = group_key_for(result)
        if "rank" not in result:
            observations.append(
                Observation(group_key, arm, Outcome.UNSCORED, detail="no rank recorded")
            )
            continue
        rank = result["rank"]
        hit = rank is not None and rank <= k
        metrics = {
            f"recall@{k_value}": float(rank is not None and rank <= k_value) for k_value in K_VALUES
        }
        metrics["reciprocal_rank"] = 1.0 / rank if rank is not None else 0.0
        observations.append(
            Observation(group_key, arm, Outcome.SCORED, score=float(hit), metrics=metrics)
        )
    return observations


def load_results(path: Path) -> list[dict]:
    """Read the per-query ``results`` list from one ``--json`` run file.

    Args:
        path: A file written by ``eval_retrieval.py --json``.

    Returns:
        The ``results`` list.

    Raises:
        ValueError: If the file has no ``results`` list to compare.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{path} has no 'results' list — was it written by eval_retrieval --json?")
    return results


def compare_runs(
    baseline_path: Path,
    candidate_path: Path,
    k: int = max(K_VALUES),
    baseline_name: str | None = None,
    candidate_name: str | None = None,
) -> str:
    """Compare two saved runs and build the paired report.

    Args:
        baseline_path: Baseline run's ``--json`` file.
        candidate_path: Candidate run's ``--json`` file.
        k: Hit cutoff for the pass/fail score (default: the harness max, 5).
        baseline_name: Display name; defaults to the baseline path.
        candidate_name: Display name; defaults to the candidate path.

    Returns:
        The formatted report.
    """
    baseline_name = baseline_name or str(baseline_path)
    candidate_name = candidate_name or str(candidate_path)
    result = pair_observations(
        observations_from_results(load_results(baseline_path), baseline_name, k),
        observations_from_results(load_results(candidate_path), candidate_name, k),
    )
    lift = summarize_correctness(result.pairs)
    metrics = [summarize_metric(result.pairs, f"recall@{k_value}") for k_value in K_VALUES]
    metrics.append(summarize_metric(result.pairs, "reciprocal_rank"))
    return build_report(
        result, lift, baseline_name, candidate_name, f"search_kb retrieval, hit@{k}", metrics
    )


def main() -> None:
    """CLI entry point: compare two saved runs and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline", type=Path, required=True, help="baseline run (eval_retrieval --json output)"
    )
    parser.add_argument(
        "--candidate", type=Path, required=True, help="candidate run (eval_retrieval --json output)"
    )
    parser.add_argument(
        "--k",
        type=int,
        default=max(K_VALUES),
        choices=K_VALUES,
        help="hit cutoff for the pass/fail score (default: %(default)s)",
    )
    parser.add_argument("--baseline-name", help="display name for the baseline arm")
    parser.add_argument("--candidate-name", help="display name for the candidate arm")
    parser.add_argument("--out", type=Path, help="also write the report to this path")
    args = parser.parse_args()

    report = compare_runs(
        args.baseline,
        args.candidate,
        k=args.k,
        baseline_name=args.baseline_name,
        candidate_name=args.candidate_name,
    )
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
