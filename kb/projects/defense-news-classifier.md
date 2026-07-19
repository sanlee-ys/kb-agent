# defense-news-classifier

An NLP pipeline that classifies public, defense-related news snippets into **three** labels: a **category** (what the article is about — procurement, operations, policy, technology, or industry), an **operational domain** (air, land, sea, cyber, space, or multi), and a **region** (indo-pacific, europe, middle-east, africa, americas, or global — the catch-all for both no-anchor and multi-region stories). It uses a single Anthropic API call per article with tool use to force structured output. It was developed on a 300-article synthetic set and is scored on a real, hand-labeled gold set of 54 public snippets.

Measured results at **v3.0.0** (shipped 2026-07-18), on the 54-snippet human answer key: **92.6% category / 92.6% domain / 87.0% region**. A separate scaled run (v2.1.0) grades 300 fresh judge-labeled snippets and corroborates the two older axes with roughly half the uncertainty: category **93.3%** [89.9, 95.6], domain **90.3%** [86.5, 93.2]. Region has no scaled number yet — that is v3.1.0 and unscheduled. At n=54 a single-axis figure carries roughly a ±13-point interval, so read the gold numbers as small-sample.

## Tech stack

- **anthropic** — calls the Anthropic API (`claude-sonnet-5`, the SYS-002 workhorse tier) with [tool use](https://docs.anthropic.com/en/docs/tool-use) to classify each article. Tool use forces the response *shape*, and since ADR-008 the schema is sent with `strict`, so the label enum is enforced server-side rather than being a guided prior validated after the fact. The in-code validation and retry path is retained as a belt-and-braces guard for the anomaly cases, not as the primary enforcement. Also used to generate the 300-article synthetic dataset.
- **pandas** — handles the labeled dataset (`synthetic_articles.csv`), predictions, and eval outputs (confusion matrices, per-label metrics, misclassification logs).
- **uv** — dependency management and reproducible runs via `uv.lock` (`requirements.txt` kept in sync as a pip fallback).
- **Python 3.11+** — runtime.

## Notes

The classifier and the eval/judge are kept separate; because the judge shares the model family, judge–human agreement is reported as a caveat, not proof.

**Three escalations have now been measured and declined**, which is the repo's running theme — spend is justified by an eval or it does not ship:

- A sharper category prompt that regressed accuracy (79.0% → 76.7%, reverted).
- **BM25 lexical grounding**, shipped in v2.0.0 and then **retired** in ADR-012 once it stopped beating the ungrounded classifier under an improved prompt. The retrieval code is kept dormant as the record of that negative result, which is why `rank-bm25` is still a dependency.
- **Tiered model routing**, built and measured in v2.2.0 and **declined** in ADR-013: routing moved +0 rows on both gold axes at roughly 1.97x the cost.

The shipped classifier is therefore single-model, single-call, and ungrounded. See `CHANGELOG.md` and `decisions/`.
