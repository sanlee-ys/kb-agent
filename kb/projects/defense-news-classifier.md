# defense-news-classifier

An NLP pipeline that classifies public, defense-related news snippets into **three** labels: a **category** (what the article is about — procurement, operations, policy, technology, or industry), an **operational domain** (air, land, sea, cyber, space, or multi), and a **region** (indo-pacific, europe, middle-east, africa, americas, or global — the catch-all for both no-anchor and multi-region stories). It uses a single Anthropic API call per article with tool use to force structured output. It was developed on a 300-article synthetic set and is scored on a real, hand-labeled gold set of 54 public snippets.

The current release is **v3.2.0** (2026-08-02). The human-graded results are still the ones measured at **v3.0.0** on the 54-snippet answer key — **92.6% category / 92.6% domain / 87.0% region** — because everything shipped since is eval and experiment machinery: the prompt and the single classify call are unchanged. At n=54 a single-axis figure carries roughly a ±13-point interval, so read those as small-sample.

Two scaled runs sit beside them, each a **frozen dated measurement** graded by the validated Opus judge on 300 fresh snippets rather than by a human. From v2.1.0 (2026-07-17): category **93.3%** [89.9, 95.6], domain **90.3%** [86.5, 93.2]. From v3.2.0 (2026-08-02), which finally covered the third axis: region **88.3%** [84.2, 91.5], category 91.7%, domain 89.3%. The region run's point was the interval, not the accuracy — the error bar narrows from 18 points at n=54 to 7 — and it confirmed that the region axis has one named failure mode: of 70 answer-key `global` rows, 17 were pulled to a specific region (16 to `americas`), which is about half of all region disagreements. Read every scaled figure **alongside** the human-graded numbers above, never instead of them: they measure agreement with the judge, and the judge's own disagreement with humans is itself measured on only 54 rows.

## Tech stack

- **anthropic** — calls the Anthropic API (`claude-sonnet-5`, the SYS-002 workhorse tier) with [tool use](https://docs.anthropic.com/en/docs/tool-use) to classify each article. Tool use forces the response *shape*, and since ADR-008 the schema is sent with `strict`, so the label enum is enforced server-side rather than being a guided prior validated after the fact. The in-code validation and retry path is retained as a belt-and-braces guard for the anomaly cases, not as the primary enforcement. Also used to generate the 300-article synthetic dataset.
- **pandas** — handles the labeled dataset (`synthetic_articles.csv`), predictions, and eval outputs (confusion matrices, per-label metrics, misclassification logs).
- **uv** — dependency management and reproducible runs via `uv.lock` (`requirements.txt` kept in sync as a pip fallback).
- **Python 3.11+** — runtime.

## Notes

The classifier and the eval/judge are kept separate; because the judge shares the model family, judge–human agreement is reported as a caveat, not proof.

**Escalations keep getting measured and declined**, which is the repo's running theme — spend is justified by an eval or it does not ship:

- A sharper category prompt that regressed accuracy (79.0% → 76.7%, reverted).
- **BM25 lexical grounding**, shipped in v2.0.0 and then **retired** in ADR-012 once it stopped beating the ungrounded classifier under an improved prompt. The retrieval code is kept dormant as the record of that negative result, which is why `rank-bm25` is still a dependency.
- **Tiered model routing**, built and measured in v2.2.0 and **declined** in ADR-013: routing moved +0 rows on both gold axes at roughly 1.97x the cost.
- **Retrieved labeled exemplars** (kNN few-shot), the third retrieval shape, **declined** in ADR-019 as a clean null. With ADR-012 and the ML loop's mined keyword features (ADR-018, which improved the split it could see and degraded the held-out one), that closes the retrieval question in three distinct failure modes.
- **A multi-agent review pipeline** (triage → classify → critic), built and **declined as configured** in ADR-020: the critic did fix most of the named `global` cluster, but it over-challenged far beyond its charter and did net harm elsewhere at roughly four times the calls.

The shipped classifier is therefore single-model, single-call, and ungrounded. See `CHANGELOG.md` and `decisions/`.
