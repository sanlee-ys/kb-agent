"""Measure search_kb retrieval quality against the gold set (v2 milestone).

Runs every query in eval/gold_set.yaml through agent.tools.search_kb and reports
recall@1/@3/@5 and MRR — overall, per kind, and for the adversarial slice. This
is a *retrieval* measurement, deliberately separate from end-answer quality
(see docs/notes/v2-kickoff.md): it asks whether the right chunk comes back,
not whether the agent phrased a nice answer around it.

Rank is the position of the first *chunk* whose source is an expected file, not
the position among deduplicated files. The agent consumes chunks, so one file
crowding the top slots with several chunks is a real retrieval behavior the
metric should see, not noise to collapse.

Requires an indexed KB (run scripts/index.py first); no API key — retrieval is
local. By default queries run unfiltered (the hard setting — the model often
omits `kind`); pass --kind-filter to give the retriever each query's kind.

    uv run python scripts/eval_retrieval.py
    uv run python scripts/eval_retrieval.py --kind-filter
    uv run python scripts/eval_retrieval.py --json eval/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

import yaml
from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # scripts/ runs from anywhere; agent/ import needs the root

from agent import tools  # noqa: E402

GOLD_SET = REPO_ROOT / "eval" / "gold_set.yaml"
K_VALUES = (1, 3, 5)

console = Console()


def _normalize(source: str) -> str:
    r"""Map a chunk source to forward-slash form for comparison.

    The index records kb/ sources with OS separators (kb\projects\x.md on
    Windows); the gold set uses forward slashes.
    """
    return source.replace("\\", "/")


def load_gold_set(path: Path = GOLD_SET) -> list[dict]:
    """Read the gold-set queries from eval/gold_set.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]


def returned_sources(observation: str) -> list[str]:
    """Ordered chunk sources from a search_kb observation (normalized).

    A non-success observation aborts the eval loudly: an unindexed KB measured
    as "0 recall" would be a lie, not a result.
    """
    obs = json.loads(observation)
    if obs["status"] != "success":
        raise RuntimeError(
            f"search_kb returned {obs['status']!r}: {obs.get('summary', '(no summary)')}"
        )
    return [_normalize(chunk["source"]) for chunk in obs["payload"]]


def rank_of_first_hit(sources: list[str], expected: set[str]) -> int | None:
    """1-based position of the first chunk from an expected file, else None."""
    for position, source in enumerate(sources, start=1):
        if source in expected:
            return position
    return None


def evaluate(
    queries: list[dict],
    search_fn: Callable[..., str] = tools.search_kb,
    kind_filter: bool = False,
) -> list[dict]:
    """Run every gold-set query; return per-query results with the hit rank."""
    results = []
    for q in queries:
        observation = search_fn(
            query=q["query"],
            kind=q["kind"] if kind_filter else None,
            n_results=max(K_VALUES),
        )
        sources = returned_sources(observation)
        expected = {_normalize(s) for s in q["expected_sources"]}
        results.append(
            {
                "id": q["id"],
                "kind": q["kind"],
                "adversarial": "adversarial" in q.get("tags", []),
                "rank": rank_of_first_hit(sources, expected),
                "returned": sources,
                "expected": sorted(expected),
            }
        )
    return results


def _metrics(results: list[dict]) -> dict:
    n = len(results)
    ranks = [r["rank"] for r in results]
    return {
        "n": n,
        **{
            f"recall@{k}": sum(1 for r in ranks if r is not None and r <= k) / n
            for k in K_VALUES
        },
        "mrr": sum(1 / r for r in ranks if r is not None) / n,
    }


def summarize(results: list[dict]) -> dict[str, dict]:
    """Metrics overall, per kind, and for the adversarial slice."""
    slices: dict[str, list[dict]] = {"overall": results}
    for kind in ("projects", "libraries", "notes"):
        slices[kind] = [r for r in results if r["kind"] == kind]
    slices["adversarial"] = [r for r in results if r["adversarial"]]
    return {name: _metrics(rs) for name, rs in slices.items() if rs}


def _print_report(results: list[dict], summary: dict[str, dict]) -> None:
    table = Table(title="search_kb retrieval vs. eval/gold_set.yaml")
    table.add_column("slice")
    table.add_column("n", justify="right")
    for k in K_VALUES:
        table.add_column(f"recall@{k}", justify="right")
    table.add_column("MRR", justify="right")
    for name, m in summary.items():
        table.add_row(
            name,
            str(m["n"]),
            *(f"{m[f'recall@{k}']:.3f}" for k in K_VALUES),
            f"{m['mrr']:.3f}",
        )
    console.print(table)

    misses = [r for r in results if r["rank"] is None]
    if misses:
        console.print("\n[bold red]Misses[/bold red] (expected file not in top "
                      f"{max(K_VALUES)}):")
        for r in misses:
            console.print(f"  [red]{r['id']}[/red] expected {r['expected']}")
            console.print(f"    got: {r['returned']}")
    late = [r for r in results if r["rank"] is not None and r["rank"] > 1]
    if late:
        console.print("\n[yellow]Hits below rank 1:[/yellow]")
        for r in late:
            console.print(f"  {r['id']}: rank {r['rank']}")


def main() -> None:
    """CLI entry point: run the eval and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--kind-filter",
        action="store_true",
        help="pass each query's kind to search_kb (default: search all kinds)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="also write per-query results and the summary as JSON",
    )
    args = parser.parse_args()

    results = evaluate(load_gold_set(), kind_filter=args.kind_filter)
    summary = summarize(results)
    _print_report(results, summary)

    if args.json:
        args.json.write_text(
            json.dumps({"summary": summary, "results": results}, indent=2),
            encoding="utf-8",
        )
        console.print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
