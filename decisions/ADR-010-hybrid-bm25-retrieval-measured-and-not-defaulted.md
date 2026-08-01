# ADR-010: Build hybrid BM25+dense retrieval, measure it, and keep dense-only as the default

**Status:** Accepted
**Date:** 2026-08-02
**Deciders:** San Lee

---

## Context

`search_kb` has been dense-only since the beginning: one ChromaDB query against embeddings
from the local `all-MiniLM-L6-v2` model. Until v2 there was no way to say whether that was
the right choice — `docs/notes/v2-kickoff.md:48` is blunt about it: "Every quality claim about
`search_kb` today is a vibe."

The v2 milestone fixed the measurement problem first: a 27-query gold set
([ADR-004](ADR-004-retrieval-gold-set-scope.md)), a recall@k/MRR harness
(`scripts/eval_retrieval.py`), and a paired A/B layer (`scripts/eval_compare.py`, #70). Step 3
of the kickoff is what this ADR closes:

> **Only then, a change worth measuring.** The obvious candidate is **hybrid retrieval**
> (lexical BM25 + the current `all-MiniLM-L6-v2` embeddings) — pointedly, the classifier's own
> eval found plain BM25 competitive on its corpus, so the question "does dense retrieval
> actually beat lexical on kb-agent's short, jargon-heavy stubs?" is live, not settled. Build
> the alternative, run it against the gold set, and **ship the negative result if the lift is
> marginal** — same bar as the rest of the portfolio.
> (`docs/notes/v2-kickoff.md:63-67`)

Two specific pieces of evidence pointed at hybrid:

1. **The four adversarial gold queries were designed for exactly this decision.** They are two
   jargon-vs-paraphrase pairs "placed where lexical and dense retrieval should *disagree*…
   These four carry the hybrid-retrieval decision" (`docs/notes/v2-kickoff.md:91-94`).
2. **The two surviving unfiltered misses looked like the textbook split.** After #71 filtered
   repo scaffolding out of the notes ingest, the baseline sat at recall@1 0.741 / recall@5
   0.926 / MRR 0.807 with two misses: `proj-03` and `adv-04`. `adv-04` is the *paraphrase*
   half of the SYS-003 pair — the classic dense failure a lexical leg is supposed to rescue.

So the hypothesis was concrete and falsifiable: adding a BM25 leg should recover `adv-04`,
because a paraphrase that dense retrieval gets wrong is where lexical matching earns its keep.

## Decision

**1. Build the hybrid path for real, and keep it in the tree.** `agent/tools.py` gains a
lexical leg — BM25 over the same chunk corpus already in the ChromaDB collection, read back
with `collection.get()` and fitted in memory — fused with the dense ranking by **Reciprocal
Rank Fusion at k=60**. `search_kb(..., hybrid=...)` selects the mode; `HYBRID_RETRIEVAL` is
the module-level default when a caller passes nothing.

**2. `HYBRID_RETRIEVAL = False`. Dense-only stays the shipped default.** Not because the
hybrid path is unfinished, but because the gold set said so. See the numbers below.

**3. Record the negative result rather than deleting the code.** The kickoff's bar was "ship
the negative result if the lift is marginal." Keeping both paths live behind one constant is
what makes the result *re-checkable* — `--hybrid` re-runs the losing arm from the same
checkout, so the next person who wants to revisit this (a bigger corpus, a different embedding
model) re-measures instead of re-implementing.

### The numbers

Both arms, same checkout, same 260-chunk / 44-file index, no source edit between them
(`scripts/eval_retrieval.py --hybrid`). Harness health clean on both comparisons: 27 groups,
27 structural pairs, 27 eligible pairs, zero dropped rows.

**Unfiltered (the hard setting — the model often omits `kind`):**

| slice | recall@1 | recall@3 | recall@5 | MRR |
|---|---|---|---|---|
| dense-only | **0.741** | 0.852 | 0.926 | 0.807 |
| hybrid RRF | 0.704 | **0.889** | **0.963** | 0.807 |

Paired hit@5: 92.6% → 96.3%, **+3.7%**. Per-pair: candidate 1, baseline 0, **ties 26**.
**McNemar exact p = 1.0000** over the single discordant pair.

**With `--kind-filter`:**

| slice | recall@1 | recall@3 | recall@5 | MRR |
|---|---|---|---|---|
| dense-only | **0.963** | 1.000 | 1.000 | **0.975** |
| hybrid RRF | 0.926 | 1.000 | 1.000 | 0.963 |

Paired hit@5: 100.0% → 100.0%, **+0.0%**. Per-pair: candidate 0, baseline 0, ties 27.

**Every rank that moved, unfiltered** (everything else was rank 1 in both arms):

| query | dense | hybrid | |
|---|---|---|---|
| `proj-03` | miss | **3** | the only pass flip in the whole run |
| `lib-03` | 2 | **1** | |
| `proj-05` | 4 | **2** | |
| `proj-08` | 5 | **4** | |
| `note-02` | 3 | **2** | |
| `lib-04` | **1** | 2 | lost a rank-1 |
| `note-10` | **1** | 2 | lost a rank-1 |
| `proj-01` | **2** | 5 | |
| `adv-04` | miss | miss | **unchanged** |

With `--kind-filter` only two ranks moved: `note-02` 3 → 2 (better) and `note-10` 1 → 2
(worse) — which is the whole −0.037 recall@1 and −0.012 MRR.

### Why dense-only wins on this evidence

**The one thing hybrid was built to fix, it does not fix.** `adv-04` — "what shape do the
agent's tool results take, and how does it recover when a tool fails" — is still a miss under
hybrid. Running the lexical leg alone explains why: BM25's own top three for that query are all
`learning-notes/05-the-agentic-tool-use-loop.md`, with `kb/projects/kb-agent.md` only fourth.
The paraphrase's surface terms (*agent*, *tool*, *results*, *recover*, *fails*) point at the
notes file just as hard as the embedding does.

**That falsifies the premise the experiment was built on.** `adv-04` is not a dense-vs-lexical
failure. Both retrievers prefer the same wrong file, so it is a **corpus-crowding** failure — a
topically-adjacent notes file out-competing the project stub — and no fusion of two retrievers
that agree can fix it. The same reading applies to `proj-03`: hybrid rescues it to rank 3, but
BM25 alone also puts two notes files above the classifier stub. Both surviving misses are the
same problem, and it is not the one hybrid addresses.

**The aggregate is a wash bought with a regression.** Unfiltered, hybrid trades depth for
precision: it pulls one miss into the top 5 (+3.7% hit@5) while costing a net rank-1 (−3.7%
recall@1). **MRR is identical to four decimal places — 0.8068 on both arms.** One discordant
pair out of 27 at p=1.0000 is not a result; it is a coin landing once. And under
`--kind-filter` — the mode the tool description actively steers the model toward — hybrid is
strictly worse on every metric that moves at all.

**A default should not be changed on a wash.** The lexical leg is a second retriever, a second
dependency, an in-memory index, and a cache-invalidation surface, all carried on every
`search_kb` call by both transports. That is real cost against an aggregate benefit of exactly
zero MRR and a net loss at rank 1.

### Fusion design, for the record

- **RRF, not a weighted score blend.** A cosine distance and a BM25 score share no scale. Any
  weighted blend needs a normalization constant, and the only data available to fit it is the
  same 27 queries the result is judged by — fitting on the test set. RRF fuses *ranks*, so
  there is nothing to fit.
- **k = 60, the canonical Cormack et al. (2009) value, deliberately not tuned.** Tuning k here
  would be the same test-set fitting by a different route. Leaving it canonical means "hybrid
  did not win" is a statement about hybrid, not about a knob nobody turned far enough.
- **Each leg ranks `max(n_results * 5, 25)` deep before fusion.** Fusing only the top 5 would
  make RRF nearly a no-op on anything dense already ranked first.
- **Zero-score chunks are dropped from the lexical leg.** BM25 orders documents containing no
  query term arbitrarily; feeding that tail into RRF would inject rank signal where there is no
  lexical evidence at all.
- **`kind` filters both legs, after scoring.** Term statistics (IDF, average document length)
  stay corpus-wide, so a filtered query returns the unfiltered relative order minus the
  excluded kinds — mirroring the dense leg, whose embeddings are likewise kind-independent.
- **The BM25 index is cached per process, keyed by a SHA-256 of the stored `(id, text)`
  pairs.** Keying on chunk *count* would have been cheaper and wrong: `index.py`'s incremental
  path routinely rewrites chunk text without changing the count.

### The dependency

`rank-bm25>=0.2.2`, pre-approved by the kickoff's own constraint — "A lexical retriever should
be a light addition (e.g. `rank-bm25`), not a heavy framework"
(`docs/notes/v2-kickoff.md:169-170`) — and the same package the defense-news-classifier chose
for its own BM25 retriever.

**The zero-dependency alternative was hand-rolling BM25.** Okapi BM25 is roughly 30 lines: term
frequencies, document lengths, an IDF table, and the scoring formula. It was rejected, but the
call is closer than it looks. In its favor: no new dependency at all, and full control of
tokenization. Against it, decisively for *this* change: a hand-rolled scorer would have been
**new untested code sitting underneath the very measurement it exists to produce**. If hybrid
had lost with a hand-rolled BM25, the first question would be whether the implementation or the
idea was at fault — and answering it would cost more than the dependency. `rank-bm25` is pure
Python, ~300 lines, its only runtime requirement is `numpy` (already present transitively via
`chromadb`), and it is what the sibling project measured against. Borrowing a known-good scorer
keeps the negative result attributable to the *approach*.

## Downstream surfaces

- **`agent/tools.py`** — the implementation and the `HYBRID_RETRIEVAL` constant. Flipping the
  default is a one-line change; anyone who does it must re-run both eval arms and amend this
  ADR with new numbers.
- **`CLAUDE.md`** — Commands block gains `eval_retrieval.py --hybrid`; Architecture §3 states
  the shipped default. This ADR is the why; `CLAUDE.md` is the do-this.
- **`scripts/eval_retrieval.py`** — `--hybrid` selects the arm via the existing `search_fn`
  parameter. Metrics, gold set, and scoring are untouched, by design: the harness that judges
  the experiment must not change with it.
- **`mcp_server/server.py`** — needs no change and got none. It calls `search_kb` with explicit
  keyword arguments and inherits the module default, which is the point of putting the switch
  on the function rather than in each transport.
- **`TOOLS` (in `agent/tools.py`)** — deliberately *not* extended with a `hybrid` property.
  Retrieval mode is an operator/experiment decision, not something for the model to pick per
  call; exposing it would put an unmeasurable degree of freedom in the agent loop.
- **`pyproject.toml` / `uv.lock`** — `rank-bm25>=0.2.2` is now a hard dependency even though
  the default path does not use it. Accepted: making it optional would mean the losing arm
  cannot be re-measured without an install step, which defeats keeping it.
- **`eval/gold_set.yaml`** — untouched, as required. Its `lib-01` query already expects
  `kb/libraries/rank-bm25.md`, so the stub for this dependency predates the dependency.
- **`docs/notes/v2-kickoff.md`** — step 3 of "What v2 is now" is the origin prose. Left as the
  investigation record; this ADR is the decision.
- **`tests/test_hybrid_retrieval.py`** — 20 offline tests over the fusion function, the lexical
  leg's filter/cutoff rules, cache invalidation, and both `search_kb` modes against a fake
  collection.

## Consequences

- **The v2 quality claim is now earned in both directions.** kb-agent can say its retrieval was
  measured *and* that the obvious improvement was measured and declined. The second half is the
  harder claim to make and the more useful one.
- **A second retrieval path is now maintained but not exercised in production.** Its only
  regular exercise is the unit tests; a change to chunk metadata or the collection shape could
  break it silently in the shipped path's shadow. The tests are the mitigation, and they are
  load-bearing for that reason.
- **`rank-bm25` is a dependency that the default code path never calls.** Small (pure Python,
  numpy-only), but it is real surface for dependency scanning and updates.
- **The result is n=27 on one KB.** It is a per-KB measurement, not a claim that hybrid
  retrieval is useless — the kickoff's own constraint (`docs/notes/v2-kickoff.md:172-173`).
  With 260 chunks and 44 files, one query is worth 3.7 percentage points, which is why the
  McNemar line matters more than the recall deltas.
- **The next retrieval question is now a different one.** Both surviving misses are corpus
  crowding — notes files out-competing project stubs for project questions — not a
  dense-vs-lexical gap. That points at chunking, at stub content, or at the `kind` routing the
  model does or does not perform, and it is the measurement that would actually move `adv-04`.
  Deliberately out of scope here.
- **`--kind-filter` remains the strong setting and the gap widened slightly.** Dense-only with
  a kind filter is 0.963 recall@1 against 0.741 unfiltered. How often the model actually
  supplies `kind` is unmeasured and is an end-to-end question, not a retrieval one.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Flip the default to hybrid on the +3.7% hit@5 | The lift is one discordant pair out of 27 at McNemar p=1.0000, MRR is identical to four decimals, recall@1 *drops*, and the `--kind-filter` arm is strictly worse. Changing a default on that is reading noise as signal |
| Delete the hybrid code once it lost | The bar was "ship the negative result," and a negative result nobody can re-run is an assertion. Keeping both arms behind one constant makes it re-checkable from the same checkout |
| Weighted score blend (α·dense + (1−α)·BM25) instead of RRF | Needs a normalization constant fitted on the only 27 queries available to judge the outcome — fitting on the test set. RRF fuses ranks and has nothing to fit |
| Tune RRF's k until hybrid wins | Same test-set fitting, one step removed. k=60 is the canonical value; leaving it untouched is what makes "hybrid did not win" a statement about hybrid |
| Hand-roll BM25 to avoid the dependency | ~30 lines and genuinely tempting, but it would put new untested code *underneath the measurement it produces* — a loss would be ambiguous between the idea and the implementation. `rank-bm25` is what the classifier measured against, so the negative result stays attributable to the approach |
| Make `rank-bm25` an optional extra since the default does not use it | The losing arm would then need an install step to re-measure, which defeats the reason for keeping it |
| Build the BM25 index over a kind-filtered corpus instead of filtering after scoring | Term statistics would shift with the filter, so a filtered query would return a different relative order than the same query unfiltered. The dense leg's embeddings are kind-independent; the lexical leg matches that |
| Cache the BM25 index on `collection.count()` | Cheaper, and wrong: `index.py`'s incremental path rewrites chunk text without changing the count ([ADR-007](ADR-007-incremental-index-and-stub-protection.md)), so a stale index would survive a re-index |
| Expose `hybrid` in the `TOOLS` schema so the model can choose | Adds an unmeasurable degree of freedom to the agent loop. Retrieval mode is an operator decision backed by an eval, not a per-call judgment call |
| Re-run the A/B on a larger gold set before deciding | The gold-set scope is settled at 27 ([ADR-004](ADR-004-retrieval-gold-set-scope.md)) and re-opening it to rescue a losing candidate would be motivated reasoning. The honest statement is the one made here: at n=27 on this KB, no |
