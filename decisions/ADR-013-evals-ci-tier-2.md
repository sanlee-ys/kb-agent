# ADR-013: Retrieval evals gate the merge — kb-agent reaches SYS-017 tier 2

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** San Lee

---

## Context

[`system/SYS-017`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-017-evals-as-ci.md)
places a measured quality number on a four-rung ladder. [ADR-012](ADR-012-reconstruct-the-notes-corpus-in-ci.md)
moved this repo from tier 0 to **tier 1** on 2026-08-02. CI reconstructs the notes corpus from
a clone, builds the index, and runs both `scripts/eval_retrieval.py` arms. The merge does not
depend on the values. A non-zero exit means the harness broke, not that retrieval got worse.

SYS-017 §3 set the order: reconstruct, then measure in CI more than once, then set floors, then
gate. ADR-012 left the last step as a later job. Two eligible CI measurements now exist:

| When | Evidence | unfiltered recall@1 / recall@5 / MRR | kind-filter recall@1 / recall@5 / MRR |
|---|---|---|---|
| 2026-08-02 | ADR-012 first CI run ([run 30772268815](https://github.com/sanlee-ys/kb-agent/actions/runs/30772268815)) | 0.741 / 0.926 / 0.813 | 0.963 / 1.000 / 0.981 |
| 2026-09-09 | latest CI ([run 34365627772](https://github.com/sanlee-ys/kb-agent/actions/runs/34365627772)) | 0.963 / 1.000 / 0.981 | 0.963 / 1.000 / 0.981 |

The unfiltered arm improved after the 2026-08-18 parent-header change. Both arms now sit at
the same operating point: overall recall@1 **0.963**, recall@5 **1.000**, MRR **0.981**, n=27.

SYS-017's first corollary: floors come from measured runs with run-to-run noise under them,
never from aspiration. A floor set from the 2026-08-02 unfiltered recall@1 of 0.741 would
accept many new misses against the current index. That number is a dated measurement, not
the bar.

SYS-017 also says tier 2 is half a branch-protection setting. A workflow cannot declare
itself required. This record ships the floors file, the gate script, and the CI step. The
parent session sets the required check after merge. Until that setting exists, the mechanism
is in place and the effect is still a report.

## Decision

Promote this repo from SYS-017 tier 1 to **tier 2**. Four parts.

### 1. Floors sit below the current operating point, with a two-miss margin

`evals/thresholds.toml` holds measured floors for both arms, overall slice only:

| Arm | n | recall@1 | recall@5 | MRR |
|---|---|---|---|---|
| unfiltered | 27 | 0.88 | 0.92 | 0.90 |
| kind_filter | 27 | 0.88 | 0.92 | 0.90 |

The operating point is 0.963, not 0.741. The margin is two gold-set misses (2/27 ≈ 0.074).
JSON keys from `summarize()` in `scripts/eval_retrieval.py` are `recall@1`, `recall@5`,
`mrr`, and `n` under `summary.overall`. TOML keys with `@` are quoted.

These floors are not aspirational. They are the current CI number minus the two-miss margin,
rounded down. They do not encode a target we hope to hit.

### 2. `scripts/eval_gate.py` grades live CI JSON and never writes a baseline

The gate is pure and offline. It reads two JSON files that `eval_retrieval.py --json`
wrote. It grades `summary.overall` against the floors. It exits 1 when:

- any floor is breached;
- a JSON file is missing or malformed;
- `n` is not 27 (a partial snapshot is a fail, SYS-017 corollary).

It never calls the network. It never writes the baseline. CI re-measures every PR because
this eval is free and deterministic. The committed bar is the floors file. The gold set
plus the reconstructed corpus are the inputs. The JSON in `eval/ci_unfiltered.json` and
`eval/ci_kind_filter.json` is ephemeral workspace output.

### 3. CI keeps clone + index, writes JSON, and fails the job on a gate breach

`.github/workflows/ci.yml` still clones `learning-notes` and builds the index. The two eval
steps now write JSON. A new step `Retrieval eval gate (SYS-017 tier 2)` runs
`uv run python scripts/eval_gate.py`. A non-zero exit now means retrieval regressed **or**
the harness broke.

`scripts/eval_kind_usage.py` stays out of PR CI. It spends API budget.

### 4. Branch protection is a separate half, and this change does not set it

SYS-017: "Tier 2 is half a branch-protection setting." This ADR does not edit GitHub
branch protection. The parent session adds the required check after merge. Until then,
a red gate step still fails the `test` job on push and pull_request. A required-check
setting is what stops an admin override and what SYS-017 uses to place a repo at tier 2
from outside.

## Downstream surfaces

- **`evals/thresholds.toml`** — the floors. A change here is a bar change, not a comment
  edit. Cite a new CI run before you move a number.
- **`scripts/eval_gate.py`** — the gate. `tests/test_eval_gate.py` grades fixture JSON
  only. Do not point that suite at a live retriever.
- **`.github/workflows/ci.yml`** — the two eval steps write JSON; the new gate step
  consumes them. Comments that said "report only" / "Tier 2 is a later job" are now
  wrong and this change rewrites them.
- **`.gitignore`** — `eval/ci_*.json` is workspace output and is not committed.
- **`CLAUDE.md`** — Commands gains `eval_gate.py`. Conventions that said SYS-017 tier 1
  and "does not gate" now describe the floors and the gate.
- **`README.md`** — the Retrieval eval section. CI now gates the merge on the floors.
- **[ADR-012](ADR-012-reconstruct-the-notes-corpus-in-ci.md)** — §4 (no floors, report
  only) is superseded. Corpus reconstruction in §1–§3 still stands.
- **[ADR-004](ADR-004-retrieval-gold-set-scope.md)** — the gate refuses n ≠ 27, so the
  27-query composition is now a CI invariant, not only a pytest check.
- **[ADR-010](ADR-010-hybrid-bm25-retrieval-measured-and-not-defaulted.md)** — still not
  a floor source. Its A/B was measured on a workstation corpus CI cannot reconstruct.
  `--hybrid` stays out of CI.
- **`system/SYS-017`** — this is the `kb-agent` 1 → 2 move. The fleet table in that
  document is a dated observation and is not enforced.
- **GitHub branch protection on `sanlee-ys/kb-agent`** — not edited here. The parent
  session adds the required check after merge.

## Consequences

- **A retrieval regression now fails the PR.** Two new misses on the gold set trip
  recall@1. A broken index build still fails earlier, at `scripts/index.py`.
- **A truncated gold set cannot sneak through.** If a run reports n=26 with perfect
  recall, the gate refuses to grade. That is the SYS-017 liveness clause.
- **CI still depends on `learning-notes` `main`, not a pinned SHA.** ADR-012 named this
  as a flapping-gate risk at tier 2. The two-miss margin absorbs small corpus drift.
  Pin the clone if the gate flaps on notes edits that this repo did not make. Do not
  pin in this change: a pin costs a bump chore, and the current operating point has
  been stable across both arms.
- **Tier 2 is incomplete until branch protection requires the `test` job.** The
  workflow change is the mechanism. The setting is the other half. This ADR records
  that split so a later reader does not treat a green required-check list as proof.
- **`eval_kind_usage.py` remains ungated.** Paid, non-deterministic, owner-triggered
  or nowhere, per SYS-017's third corollary.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Gate on the 2026-08-02 unfiltered recall@1 of 0.741 | Too weak against the current index. That arm now measures 0.963. A floor at 0.741 would accept six extra misses and still print PASS. SYS-017 wants floors under the operating point, not under an obsolete one. |
| Commit a frozen prediction snapshot, like the classifier | Rejected. The classifier eval calls a paid, non-deterministic model, so CI grades a committed CSV and a live job re-measures on a schedule. This retrieval eval is free and deterministic, so CI re-measures every PR. SYS-017 §2 already says kb-agent gets one gate, not the classifier's two-gate split. The floors file is the committed bar. |
| Set floors at 0.963 with no margin | One miss on 27 queries drops recall@1 to 0.926 and trips the gate. The gold set is small. Two-miss margin is the noise band we can name today. Tighten later if more CI runs show less movement. |
| Gate recall@3 as well | Both arms now measure 1.000 at k=3 and k=5. recall@5 already covers "the hit is in the window the agent consumes." A third floor adds no new failure mode. |
| Pin `learning-notes` to a SHA in this change | Available later if the gate flaps. Not needed for the first floors: the 2026-09-09 run matches the kind-filter arm from 2026-08-02 to three decimals. |
| Also wire `scripts/eval_kind_usage.py` into the PR leg | It spends one model call per gold query per run. SYS-017 puts paid legs on owner-triggered lanes or nowhere. |
