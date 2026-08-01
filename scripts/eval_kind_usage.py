"""Measure how often the model passes `kind` to search_kb (v2 option b).

The retrieval eval (scripts/eval_retrieval.py) shows a large gap between its two
arms: unfiltered search is much weaker than search given the query's `kind`. That
gap is only worth closing if the *model* can be steered to supply `kind` in real
usage — so this harness measures the model's behavior, not the retriever's.

For each query in eval/gold_set.yaml it makes ONE `messages.create` call with the
real KBAgent system prompt and the real TOOLS list, then inspects the FIRST
`tool_use` block the model emits. No tools are executed and there is no loop: the
thing under measurement is the model's opening move.

Three rates are reported, overall and per kind slice:

  - **kind-pass rate**  — the first tool call is `search_kb` and it carries a
    `kind` argument (right or wrong).
  - **kind-correct rate** — that `kind` equals the gold query's `kind`.
  - **search_kb-first rate** — the opening move was `search_kb` at all. This
    isolates *tool selection* from *kind steering*: kind-pass can only ever be as
    high as this, so a shortfall here is a different defect with a different fix
    (tool descriptions) than a shortfall between the two (the `kind` steering text).

A first call to some *other* tool (e.g. `list_projects`) counts as kind-not-passed
and is recorded distinctly in the JSON as ``other_tool`` (and, as a rate, as the
complement of ``search_first_rate``), so a rate drop caused by tool selection is
auditable rather than invisible.

This is a *model-behavior* measurement, deliberately separate from retrieval
quality: it says nothing about whether the retrieved chunks were right. Pair it
with eval_retrieval.py, which answers that question and bypasses prompts entirely.

Needs ANTHROPIC_API_KEY (27 model calls per run). No index required.

    uv run python scripts/eval_kind_usage.py
    uv run python scripts/eval_kind_usage.py --json eval/kind-baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # scripts/ runs from anywhere; agent/ import needs the root

from agent.agent import DEFAULT_MODEL, SYSTEM_PROMPT  # noqa: E402
from agent.tools import TOOLS  # noqa: E402
from scripts.eval_retrieval import load_gold_set  # noqa: E402

SEARCH_TOOL = "search_kb"
KINDS = ("projects", "libraries", "notes")
# Enough room for the model to think briefly and emit one tool_use block.
MAX_TOKENS = 1024

console = Console()


def _field(block, name: str):
    """Read a field from a content block that may be an SDK object or a dict.

    The live path hands us Anthropic SDK content blocks; the offline tests hand us
    plain dicts. Both are read the same way here so the parsing logic under test is
    the same logic that runs against the API.

    Args:
        block: A response content block (SDK object or dict).
        name: The field to read (``type``, ``name``, ``input``).

    Returns:
        The field's value, or None if the block doesn't carry it.
    """
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def first_tool_use(blocks) -> dict | None:
    """Extract the first ``tool_use`` block from a response's content.

    Args:
        blocks: The response's content blocks, in order.

    Returns:
        ``{"name": str, "input": dict}`` for the first tool call, or None if the
        model answered without calling a tool.
    """
    for block in blocks or []:
        if _field(block, "type") == "tool_use":
            tool_input = _field(block, "input")
            return {
                "name": _field(block, "name"),
                "input": tool_input if isinstance(tool_input, dict) else {},
            }
    return None


def grade_call(call: dict | None, expected_kind: str) -> dict:
    """Grade one opening tool call against the gold query's kind.

    A call to a tool other than ``search_kb`` (or no tool call at all) is *not* a
    kind pass — the model never got as far as filtering — but it is flagged
    separately so the two failure modes stay distinguishable.

    Args:
        call: The first tool call, as returned by :func:`first_tool_use`.
        expected_kind: The gold-set ``kind`` for this query.

    Returns:
        A dict with ``tool``, ``passed_kind``, ``kind_passed``, ``kind_correct``
        and ``other_tool``.
    """
    tool = call["name"] if call else None
    other_tool = tool != SEARCH_TOOL
    passed_kind = None if other_tool else call["input"].get("kind")
    kind_passed = passed_kind in KINDS
    return {
        "tool": tool,
        "passed_kind": passed_kind,
        "kind_passed": kind_passed,
        "kind_correct": kind_passed and passed_kind == expected_kind,
        "other_tool": other_tool,
    }


def evaluate(queries: list[dict], call_fn: Callable[[str], object]) -> list[dict]:
    """Ask the model each gold-set query and grade its opening tool call.

    Args:
        queries: Gold-set entries (``id``/``kind``/``query``/``tags``).
        call_fn: Takes the query text and returns the response's content blocks.

    Returns:
        One graded result per query, in gold-set order.
    """
    results = []
    for q in queries:
        call = first_tool_use(call_fn(q["query"]))
        results.append(
            {
                "id": q["id"],
                "query": q["query"],
                "expected_kind": q["kind"],
                "adversarial": "adversarial" in q.get("tags", []),
                **grade_call(call, q["kind"]),
            }
        )
    return results


def _rates(results: list[dict]) -> dict:
    n = len(results)
    other = sum(1 for r in results if r["other_tool"])
    return {
        "n": n,
        "kind_pass_rate": sum(1 for r in results if r["kind_passed"]) / n,
        "kind_correct_rate": sum(1 for r in results if r["kind_correct"]) / n,
        "search_first_rate": (n - other) / n,
        "other_tool": other,
    }


def summarize(results: list[dict]) -> dict[str, dict]:
    """Rates overall, per expected kind, and for the adversarial slice.

    Args:
        results: Graded per-query results from :func:`evaluate`.

    Returns:
        Slice name -> rates. Empty slices are omitted rather than divided by zero.
    """
    slices: dict[str, list[dict]] = {"overall": results}
    for kind in KINDS:
        slices[kind] = [r for r in results if r["expected_kind"] == kind]
    slices["adversarial"] = [r for r in results if r["adversarial"]]
    return {name: _rates(rs) for name, rs in slices.items() if rs}


def _print_report(results: list[dict], summary: dict[str, dict], model: str) -> None:
    table = Table(title=f"search_kb kind usage vs. eval/gold_set.yaml ({model})")
    table.add_column("slice")
    table.add_column("n", justify="right")
    table.add_column("kind pass", justify="right")
    table.add_column("kind correct", justify="right")
    table.add_column("search_kb first", justify="right")
    table.add_column("other tool", justify="right")
    for name, m in summary.items():
        table.add_row(
            name,
            str(m["n"]),
            f"{m['kind_pass_rate']:.3f}",
            f"{m['kind_correct_rate']:.3f}",
            f"{m['search_first_rate']:.3f}",
            str(m["other_tool"]),
        )
    console.print(table)

    missing = [r for r in results if not r["kind_passed"]]
    if missing:
        console.print("\n[bold red]No kind passed[/bold red]:")
        for r in missing:
            if not r["other_tool"]:
                reason = "search_kb without kind"
            elif r["tool"] is None:
                reason = "no tool call"
            else:
                reason = f"called {r['tool']}"
            console.print(f"  [red]{r['id']}[/red] ({r['expected_kind']}) — {reason}")
    wrong = [r for r in results if r["kind_passed"] and not r["kind_correct"]]
    if wrong:
        console.print("\n[yellow]Wrong kind:[/yellow]")
        for r in wrong:
            console.print(
                f"  {r['id']}: passed {r['passed_kind']!r}, "
                f"expected {r['expected_kind']!r}"
            )


def _api_caller(client: anthropic.Anthropic, model: str) -> Callable[[str], object]:
    """Build a call_fn that sends one query as a single, tool-enabled turn.

    Mirrors how ``KBAgent.ask`` constructs its request — same system prompt, same
    TOOLS list, same model — but stops after the first response instead of running
    the tool-use loop, because the measurement is the model's opening move.

    No ``temperature`` is sent: the workhorse model rejects the parameter as
    deprecated (HTTP 400). Sampling is therefore not pinned, which is why a single
    run is not authoritative — repeat the run and report the median and spread
    rather than one draw.

    Args:
        client: An Anthropic client.
        model: Model id to measure.

    Returns:
        A function from query text to the response's content blocks.
    """

    def call(query: str):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=[{"role": "user", "content": query}],
        )
        return response.content

    return call


def main() -> None:
    """CLI entry point: run the kind-usage eval and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        help="model id to measure (default: KB_AGENT_MODEL or the agent's DEFAULT_MODEL)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="also write per-query results and the summary as JSON",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    model = args.model or os.environ.get("KB_AGENT_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()

    results = evaluate(load_gold_set(), _api_caller(client, model))
    summary = summarize(results)
    _print_report(results, summary, model)

    if args.json:
        args.json.write_text(
            json.dumps({"model": model, "summary": summary, "results": results}, indent=2),
            encoding="utf-8",
        )
        console.print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
