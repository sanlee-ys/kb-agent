# ADR-004: Scope the retrieval gold set at 27 weighted queries, with no LLM judge

**Status:** Accepted
**Date:** 2026-07-17 (recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

v2 was rescoped inward: rather than factoring kb-agent's retrieval into a shared backbone for
another repo, v2 measures kb-agent's own retrieval quality first. The prose states the irony
that forced this plainly — the retired backbone plan *assumed* kb-agent's retrieval was good
enough to be a substrate for another project, but "kb-agent has **never measured its own
retrieval quality.** There is no gold set, no recall@k, no confusion between 'the agent
answered well' and 'the right chunk was retrieved.' Every quality claim about `search_kb`
today is a vibe" (`docs/notes/v2-kickoff.md:44-48`).

One scoping question survived the pivot, because it was always about measurement rather than
about the departed consumer: **how do you measure quality with no model-made answer key?**
The prose answers that for retrieval this is more tractable than the classifier's version of
the same problem, because the label is `query → expected source`, which a human can check
directly against the KB (`docs/notes/v2-kickoff.md:75-80`).

That question was closed with San on **2026-07-17** and written up under *"Settled: gold-set
scope"* (`docs/notes/v2-kickoff.md:82-98`), explicitly so the harness would build against a
settled scope "not against a fresh debate." The gold set now exists at `eval/gold_set.yaml`
and the harness at `scripts/eval_retrieval.py`.

**This ADR records a decision that was already made and already written down.** Nothing here
is new reasoning; it is the kickoff note's settled section moved onto the shelf the
`decisions/` README already listed it as owing (`decisions/README.md:41`).

## Decision

**A 27-query hand-labeled retrieval gold set, weighted to the real corpus, with no LLM judge
at the retrieval layer.** Four parts, as settled:

1. **27 queries, weighted — not an even three-way split.** The indexed corpus is lopsided:
   roughly 30 notes files (the learning-notes set plus glossary) against 2 project stubs and
   2 library stubs. The stated ground for weighting is representativeness: "An even split
   would misrepresent what retrieval actually searches." The split is **8 projects / 5
   libraries / 10 notes / 4 adversarial**.
2. **Adversarial queries are a tag, not a kind.** Each of the four is one of a pair — an
   exact-jargon phrasing versus a paraphrase of the same need — placed where lexical and
   dense retrieval should *disagree*, and spread across kinds. "These four carry the
   hybrid-retrieval decision; the other 23 establish the baseline."
3. **Labels are `query → expected source file(s)`**, matching the chunk `source` metadata,
   hand-labeled by San. **No LLM judge at the retrieval layer.**
4. **Metrics: recall@1/@3/@5 and MRR**, with k=5 chosen to match `search_kb`'s default
   `n_results`. Home: `eval/gold_set.yaml`, in-repo, public/synthetic-safe.

**Why no judge — the interesting call.** The record gives one affirmative reason: the label
is objective, so a judge would be answering a question a human has already answered exactly.
"the gold set is `query → expected source`, which a human can label directly against the KB
(no LLM judge needed for recall@k / MRR)" (`docs/notes/v2-kickoff.md:76-77`). The
retrieval question is "did the right chunk come back in the top *k*" — a set-membership
check against a labeled file path, not a judgment call. There is nothing for a judge to
adjudicate.

The second reason is separation of layers, and it is stated as a boundary rather than a
rejection of judges generally: retrieval measurement is "deliberately *separate* from
end-answer quality; conflating the two is how RAG systems hide bad retrieval behind a capable
model" (`docs/notes/v2-kickoff.md:60-61`). The note explicitly leaves the door open one layer
up: "If end-answer quality gets measured too, *that* layer may want an LLM judge — keep it
distinct from the retrieval metric" (`docs/notes/v2-kickoff.md:79-80`). So this is *not* a
decision that judges are unwarranted in this repo. It is a decision that the retrieval metric
specifically does not need one, and that letting a judge straddle both layers would destroy
the property the split exists to protect.

**On the classifier cross-reference:** the source prose does invoke the classifier twice near
this decision — as the source of the *measure-first* discipline v2 is applying
(`docs/notes/v2-kickoff.md:50-51`), and as the reason hybrid retrieval is a live question,
since "the classifier's own eval found plain BM25 competitive on its corpus"
(`docs/notes/v2-kickoff.md:63-65`). It also names "the classifier's version" of the
answer-key problem as the harder one (`docs/notes/v2-kickoff.md:75-76`). **The prose does
not, however, reference the classifier's judge-validation practice** (agreement scoring
against human labels) as grounds for this call. That connection is not preserved in the
record and is not asserted here.

## Downstream surfaces

- `eval/gold_set.yaml` — the artifact this scope defines. Its header comment restates the
  scope and cites this section of the kickoff note.
- `scripts/eval_retrieval.py` and `tests/test_eval_retrieval.py` — the harness built against
  this scope, and its tests.
- `CLAUDE.md` Commands § — documents `eval_retrieval.py` and its `--kind-filter` variant. The
  operative instruction stays there; this ADR carries the why.
- `docs/notes/v2-kickoff.md` — the original home of this record, retained. Also carries two
  attached items this ADR does not restate as decisions: **step 0** (full ingest → `--accept`
  → `index.py`, so labeling does not bake in three known corpus gaps) and the **deliberate
  non-fix** of `index.py` rglobbing `CLAUDE.md` / `README.md` / `graphify-out/` into the
  `notes` kind — left in place on purpose, because whether that noise hurts retrieval is a
  question the gold set exists to answer with a number.
- `decisions/README.md` "Still to migrate" list — this decision is item 2 and should be
  struck once registered in the index table.
- Any later end-answer-quality eval — bound by the "keep it distinct" clause above. That
  layer may adopt a judge; it must not merge into the retrieval metric.

## Consequences

- **The retrieval numbers are cheap, offline, and deterministic.** No API key, no judge
  calls, no per-run cost or variance. `eval_retrieval.py` is documented as running offline
  against an indexed KB.
- **The measurement is honest about what it is not.** recall@k and MRR say the right chunk
  was retrievable. They say nothing about whether the agent's final answer was good. That is
  the intended scope, but the claim must be stated that narrowly.
- **The cost of no judge is real:** anything requiring a judgment call — was the answer
  faithful, was a *different* returned chunk also acceptable — is out of reach at this layer.
  Binary source-match labeling also means a retrieval that surfaces a genuinely useful but
  unlabeled chunk scores as a miss. Mitigated only partly by allowing multiple
  `expected_sources` per query (a hit if *any* appears in top k).
- **The weighting binds the numbers to this corpus.** Results are a per-KB measurement, not a
  universal claim about dense versus lexical retrieval — a constraint the kickoff note
  carries forward independently (`docs/notes/v2-kickoff.md:172-173`).
- **27 queries is small, and 4 adversarial is very small.** The four pairs carry the
  hybrid-retrieval decision on their own; a fork resting on 4 data points should be read with
  that in mind.
- **One nuance the ADR should not smooth over:** the prose says "Hand-labeled by San," while
  `eval/gold_set.yaml:15-20` records that queries and proposed labels were drafted by Claude
  against the actual stub contents, with San's per-entry review before merge serving as the
  hand-label pass. The same header states the discipline that keeps this sound — do not tune
  queries to what the current retriever returns; the gold set is what *should* come back and
  must stay independent of any retriever under test.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Even three-way split across projects / libraries / notes | Rejected on representativeness: the corpus is ~30 notes files against 2 project and 2 library stubs, so an even split "would misrepresent what retrieval actually searches" |
| An LLM judge at the retrieval layer | Nothing for it to adjudicate — the label is an objective `query → expected source` match a human can check directly against the KB. Adds cost, variance, and a validation burden to answer a question already answered by the label |
| One eval spanning retrieval *and* end-answer quality | Explicitly rejected as the failure mode: "conflating the two is how RAG systems hide bad retrieval behind a capable model." A judge straddling both layers would erase the separation the split exists to create |
| Make "adversarial" a fourth `kind` rather than a tag | Adversarial pairs are deliberately spread *across* kinds and must keep their real kind, so lexical-vs-dense disagreement is probed inside each corpus slice rather than isolated in its own bucket |
| Label against the KB as it stood | Rejected via **step 0**: three known gaps (`kb/projects/notes-api.md` missing, only 2 library stubs, no `.ingest-manifest.json`) would have been baked into the labels — "a 5-query library slice over 2 files measures nothing" |
| Fix the `index.py` rglob noise before labeling | Deliberately left in place. Whether indexing `CLAUDE.md` / `README.md` / `graphify-out/` as `notes` hurts retrieval is exactly what the gold set should answer with a number; if it shows in the misses, filtering becomes a measured change like any other |
| Defer the scope and decide it while building the harness | Rejected in the prose itself: decide size and query selection "**before** building the harness," so the harness "builds against this, not against a fresh debate" |
