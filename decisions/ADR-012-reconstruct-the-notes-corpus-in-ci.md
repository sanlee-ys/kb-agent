# ADR-012: Reconstruct the notes corpus in CI from a clone, and fail on an absent one — `kb-agent` reaches SYS-017 tier 1

**Status:** Accepted
**Date:** 2026-08-02
**Deciders:** San Lee

---

## Context

[`system/SYS-017`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-017-evals-as-ci.md)
was adopted on 2026-08-02 with a four-rung ladder, and it places this repo at **tier 0** —
"measured, not gated." `scripts/eval_retrieval.py` exists, is offline, needs no API key, embeds
locally with `all-MiniLM-L6-v2`, and **appears in no workflow**. What CI runs today is ruff, two
cross-repo contract checks, the ADR lint, and `pytest`. Retrieval quality is measured on the
author's workstation and quoted in prose.

SYS-017's rollout names this repo as the primary move, `0 → 1`, and puts almost all of the cost in
one step that is not CI plumbing at all: **the eval's corpus is not reconstructible in CI.**

Concretely, three facts that compound:

- **12 of the gold set's 27 queries expect sources outside this repo.** `note-01`–`note-10`,
  `adv-01`, and `adv-02` declare `expected_sources` under `learning-notes/`
  (`eval/gold_set.yaml:109-189`). Those files live in a separate repo.
- **The only pointer to them is an absolute Windows path.** `projects.yaml`'s `notes_dirs` held
  `C:\Users\sanle\code\learning-notes`, read by `scripts/index.py::notes_dirs()` from
  `REPO_ROOT / "projects.yaml"` with no override of any kind.
- **A missing notes directory was skipped with a warning.** `collect_documents()` printed
  `notes_dir not found, skipping: …` and carried on, so an index built anywhere but that one
  machine came out quietly short of the entire `notes` kind.

Wire the eval up naively against that and it scores **zero recall on 44% of the gold set** — not
because retrieval regressed, but because the documents are absent, while every step in the job
exits 0. That is SYS-017's second house corollary exactly ("a gate that cannot fail is theater"),
and it is why corpus provenance is the ladder's **tier-1 entry condition** rather than a detail of
the `kb-agent` rollout.

## Decision

Three parts, in the order SYS-017 §3 requires — reconstruct, then measure, and *only* then
consider floors.

### 1. `KB_AGENT_NOTES_DIRS` overrides `notes_dirs`, and the override wins outright

`scripts/index.py::notes_dirs()` now reads `KB_AGENT_NOTES_DIRS` — an `os.pathsep`-separated list
of directories — in preference to `projects.yaml`. It does not merge the two: a `projects.yaml`
entry naming a path that exists only on one workstation is precisely what the override exists to
displace, so merging would reintroduce the machine dependency it removes. The empty string is a
real answer meaning "index no external notes"; only an *unset* variable falls back to
`projects.yaml`.

This follows the shape already in the repo for `KB_AGENT_MODEL` (`CLAUDE.md` Conventions): an
environment variable that redirects a hard-coded constant without a source edit.

`notes_dirs()` now returns `(dirs, origin)`. The origin string is not decoration — it is what lets
the error in part 2 name the knob the reader has to turn, rather than making them guess between
two configuration surfaces.

### 2. A configured-but-absent notes directory is a hard error

`collect_documents()` raises `FileNotFoundError` instead of warning and continuing. The message
names the missing path, the origin that supplied it, both repairs (check the corpus out there, or
repoint `KB_AGENT_NOTES_DIRS`), and the opt-out (`KB_AGENT_NOTES_DIRS=""`).

**This is the load-bearing half of the change, and it is deliberately not CI-conditional.** A skip
is only safe if nothing downstream reports a number over the result, and something now does. Making
the error fire only under `CI=true` would leave the workstation able to build a short index and
publish a figure measured against it — the failure this ADR exists to close, preserved in the one
environment where the repo's published numbers actually come from. An absent directory is a
misconfiguration everywhere.

The cost is real and accepted: any machine whose checkout does not sit at the `projects.yaml` path
now fails the index build instead of silently degrading. `KB_AGENT_NOTES_DIRS` is the fix, and the
error says so.

### 3. CI clones `learning-notes`, builds the index, and runs both eval arms — reporting only

Four steps appended to `.github/workflows/ci.yml`, after the existing suite so lint and test
failures still surface first:

1. `git clone --depth 1` of `learning-notes` into `${{ runner.temp }}/learning-notes`. The repo is
   public, so no token is involved. Precedent is `architecture/.github/workflows/portal.yml`, which
   already shallow-clones this repo and two siblings.
2. `scripts/index.py` with `KB_AGENT_NOTES_DIRS` pointed at the clone. The existing
   `~/.cache/chroma` + `~/.cache/huggingface` cache step already covers the ~80MB model download,
   because the integration test needed it first.
3. `scripts/eval_retrieval.py` — the unfiltered arm.
4. `scripts/eval_retrieval.py --kind-filter`.

**The clone's directory name is part of the contract.** `index.py` records a note's `source` as
`<notes_dir name>/<filename>`, and the gold set spells them `learning-notes/…`. Cloning to any
other directory name scores all 12 notes queries as misses with nothing else looking wrong. That is
now stated in `collect_documents()`'s docstring and in the workflow comment.

**Both arms run, and that is a detection argument rather than symmetry.** The unfiltered arm is the
headline number and the harder setting. The `--kind-filter` arm is the only thing that exercises
`search_kb`'s metadata `where` path, whose failure the unfiltered arm cannot see. Two local runs
over an already-built index cost seconds.

### 4. No floors, no gate — and that is a finding, not an omission

Nothing in this change can fail the build on a *value*. `eval_retrieval.py` has no threshold logic;
a non-zero exit from these steps means the harness itself broke (unindexed KB, a non-success
observation), never that retrieval got worse.

SYS-017's rollout is explicit that **no existing number is floor-eligible**: every published figure
— including both arms of [ADR-010](ADR-010-hybrid-bm25-retrieval-measured-and-not-defaulted.md)'s
A/B — was measured on the workstation against the corpus CI could not reconstruct. A number
measured in one environment is not a floor in another. The eligible runs are the ones these steps
now produce, **plural**, because a floor needs a run-to-run noise band under it and one pass cannot
supply one. Tier 2 is a separate later job.

`scripts/eval_kind_usage.py` stays out of CI entirely. It spends one model call per gold query per
run, and SYS-017 puts paid legs on owner-triggered lanes or nowhere.

### What the reconstructed corpus actually measures

Measured on 2026-08-02, first by running the CI recipe by hand — fresh `--depth 1` clone into a
temp dir, `KB_AGENT_NOTES_DIRS` pointed at it, index built, both arms run — and then confirmed by
the CI leg itself:

| Arm | n | recall@1 | recall@3 | recall@5 | MRR |
|---|---|---|---|---|---|
| unfiltered | 27 | 0.741 | 0.852 | 0.926 | 0.813 |
| `--kind-filter` | 27 | 0.963 | 1.000 | 1.000 | 0.981 |

**The first real CI run reproduced both arms exactly** — every figure above, to three decimals,
over an identical 269 chunks from 44 files
([run 30772268815](https://github.com/sanlee-ys/kb-agent/actions/runs/30772268815), Ubuntu, against
Windows locally). That is a stronger provenance signal than either run alone: the reconstruction is
deterministic across operating systems, so the corpus really is defined by version control and not
by the machine.

**It is still not a baseline, and none of these numbers is a floor.** Two identical runs of the
same corpus do not establish a noise band — they establish that this corpus is stable, which is a
different claim. The band that tier 2 needs comes from runs spread over real changes to `kb/` and
to `learning-notes`, and those accumulate from here.

The reconstruction produces a complete measurement rather than a hole: both misses are
`projects`-kind queries losing to notes chunks, and every `notes` query the absent corpus would
have zeroed now resolves at rank 1 or 2.

It also makes SYS-017's point concretely: the index is **269 chunks over 44 files**, against the
**325 chunks** the README's 2026-07-17 figures were measured on. The workstation corpus was larger
than anything version control can rebuild. The old number and the new one are not comparable, which
is the whole reason neither is a floor.

## Downstream surfaces

- **`.github/workflows/ci.yml`** — the four new steps. This is the tier-1 mechanism; if the clone
  step is removed or renamed, the eval silently reverts to measuring an absent corpus.
- **`scripts/index.py`** — `NOTES_DIRS_ENV`, `notes_dirs()`'s changed return type, and the
  `FileNotFoundError` in `collect_documents()`. **`notes_dirs()` now returns a tuple**; it has one
  caller today, and a second one added later must unpack rather than iterate.
- **`projects.yaml`** — its `notes_dirs` entry is now a *default* rather than the only source.
  Unmodified by this ADR; the absolute path is still correct on the workstation.
- **`CLAUDE.md`** — Conventions gains the `KB_AGENT_NOTES_DIRS` rule and the hard-error behavior,
  per `decisions/README.md`'s split (the ADR is the "why", `CLAUDE.md` is the "do this").
  Architecture §2 describes `index.py` and mentions the notes sweep.
- **`README.md`** — the Retrieval eval section quotes 2026-07-17 workstation figures measured on a
  325-chunk index. Those numbers are not wrong, but they are now *un-reproducible by CI* and should
  be read as dated workstation measurements. The section gains a note saying so and pointing at
  the CI arm.
- **`tests/test_index.py`** — seven new tests covering the override precedence, multi-path parsing,
  the empty-string opt-out, and both hard-error paths (env-supplied and yaml-supplied). An autouse
  fixture clears `KB_AGENT_NOTES_DIRS` so an ambient value on the author's shell cannot leak into
  the suite.
- **`docs/notes/test-coverage-backfill.md:28`** — names "the notes walk + skip-missing in
  `collect_documents`" as an uncovered gap. The skip-missing branch no longer exists and the
  replacement is tested; the row is updated rather than left describing deleted code.
- **[ADR-010](ADR-010-hybrid-bm25-retrieval-measured-and-not-defaulted.md)** — its A/B arms are
  explicitly named by SYS-017 as *not* floor-eligible, for the reason above. The ADR's conclusion
  (dense-only) is untouched; only its numbers' eligibility as thresholds is being characterized.
  The `--hybrid` flag stays out of CI: it is an experiment hook, not a shipped path.
- **[ADR-004](ADR-004-retrieval-gold-set-scope.md)** — the 8/5/10/4 composition this decision
  refuses to shrink. SYS-017 forecloses scoping the eval to the 15 in-repo queries; that option is
  closed here too.
- **[ADR-011](ADR-011-incremental-index-by-default.md)** — CI now runs `index.py` on its default
  incremental path against a cold `chroma_db/`, so every CI run is a first-run full embed. No
  conflict; noted because it means the incremental diff is *not* what CI exercises.
- **`eval/gold_set.yaml`** — its `learning-notes/<filename>` source convention is now depended on
  by a workflow, which pins the clone's directory name.
- **`system/SYS-017`** — this is its `kb-agent` rollout row, `0 → 1`. Its fleet table still records
  this repo at tier 0 and goes stale with this merge; the table is a dated observation by its own
  classification and is not enforced.
- **`learning-notes`** — CI now depends on it being public and cloneable. If it is ever made
  private, this leg breaks and needs a token.

## Consequences

- **CI depends on a second repo.** Named as a cost by SYS-017 and accepted. A `learning-notes`
  outage or a visibility change turns this leg red. It fails loudly rather than silently, which is
  the trade being bought.
- **CI measures `learning-notes` `main`, not a pinned revision.** A note edited there can move this
  repo's retrieval numbers with no change here. At tier 1 that is only a report moving, and it is
  arguably the honest behavior — the corpus really did change. **At tier 2 it becomes a flapping
  gate**, and pinning the clone to a SHA is the obvious fix. Deliberately not done now: pinning
  costs a bump chore, and until a floor exists there is nothing for the drift to break.
- **The live `notes-api` leg is absent in CI, deterministically.** `collect_notes_from_api()` warns
  and returns empty when the service is unreachable, which it always is on a runner. No gold-set
  query expects a `notes-api/note/…` source, so the corpus is complete without it — but this means
  the CI index is a strict subset of a fully-warm workstation index, by construction.
- **A workstation whose checkout moved now fails instead of degrading.** Intended, and the error
  names the repair. This is the change most likely to surprise someone.
- **Tier 2 is now unblocked but not started.** The remaining work is: collect several CI runs to
  get a noise band, commit a baseline, write a gate script and a floors file, and make it a
  required status check. None of that is in this change.
- **The gold set's composition is preserved.** No query was dropped or filtered to make CI green,
  which was the tempting shortcut and the one SYS-017 closes in writing.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Commit a small fixture corpus under `eval/` for CI to index | Reproducible, but it measures a corpus assembled to be measurable. The gold set's `expected_sources` name real `learning-notes/` files, so a fixture means either rewriting 12 queries or shipping stand-ins under the same filenames — and the number would still be called "retrieval recall". SYS-017 rejects the sibling form of this (copying the corpus in) for drift; a purpose-built fixture is the same fork with less content. |
| Vendor a snapshot of `learning-notes` into this repo | SYS-017's rejected-alternatives table already closes this: it forks the notes, the copy drifts from the source the moment either moves, and the drift is invisible **precisely because the eval keeps passing** against the stale copy. It also makes this repo a second home for content it does not own. |
| Have CI write a `projects.yaml` before indexing | The other option SYS-017 §3 offers. Rejected because it makes CI mutate a tracked file that `ingest.py`, `ingest.py --check`, and `tools.py` all read, so a half-run job leaves a rewritten config behind. An env var is inspectable, testable without touching the filesystem, and matches `KB_AGENT_MODEL`. |
| Keep the skip-with-warning, and make the eval step assert the corpus separately | Two mechanisms where one will do, and it leaves the silent-short-index behavior reachable by every other caller of `index.py`. The check belongs where the corpus is read. |
| Make the hard error fire only when `CI` is set | Preserves the exact failure mode on the machine that produces the repo's published numbers. See §2. |
| Scope the CI eval to the 15 queries whose sources are committed under `kb/` | Foreclosed by SYS-017 in writing: it discards the entire `notes` kind plus both `rag` adversarial pairs from a composition settled in ADR-004, while still calling the result "retrieval recall". |
| Commit a prebuilt `chroma_db/` so CI need not index | Git-ignored by existing convention, binary, and large. It would also make the eval pass without proving the index *build* still works, which is a meaningful part of what this leg protects. |
| Containerize the eval to make it reproducible | SYS-017 §4 rejects containers as the *mechanism*: an image packages whatever went into it, so "where did the notes come from" survives inside it, harder to inspect. Deferred there as an optimization with a trigger (cache misses or model-version drift making the gate flap), and nothing here changes that. |
| Set floors from the numbers in this ADR and gate now | The numbers above are one local run of the CI recipe. SYS-017 and `classifier/ADR-014` both require a measured noise band under a floor, and a single pass cannot supply one. Gating on it would be an aspirational floor wearing a measured number's clothes. |
| Also wire `scripts/eval_kind_usage.py` into the PR leg | It spends one model call per gold query per run. SYS-017's third corollary puts paid legs on owner-triggered lanes or nowhere, and recommends nowhere for this one. |
