# defense-news-classifier

An NLP pipeline that classifies public, defense-related news snippets into two labels: a **category** (what the article is about — procurement, operations, policy, technology, or industry) and an **operational domain** (air, land, sea, cyber, space, or multi). It uses a single Anthropic API call per article with tool use to force structured output; the label enum is validated in code (an out-of-enum prediction is re-sampled once). It was developed on a 300-article synthetic set and is scored on a real, hand-labeled gold set of 54 public snippets. Measured results (v2): 88.9% accuracy on both category (0.906 macro-F1) and operational domain (0.894 macro-F1). The earlier synthetic-only eval (v1) read 79.0% category / 97.3% domain.

## Tech stack

- **anthropic** — calls the Anthropic API (`claude-sonnet-4-6`) with [tool use](https://docs.anthropic.com/en/docs/tool-use) to classify each article. Tool use forces the response *shape*; the label enum is a guided prior that is validated in code (an invalid label is re-sampled once), not enforced server-side. Also used to generate the 300-article synthetic dataset.
- **pandas** — handles the labeled dataset (`synthetic_articles.csv`), predictions, and eval outputs (confusion matrices, per-label metrics, misclassification logs).
- **uv** — dependency management and reproducible runs via `uv.lock` (`requirements.txt` kept in sync as a pip fallback).
- **Python 3.11+** — runtime.

## Notes

The classifier and the eval/judge are kept separate; because the judge shares the model family, judge–human agreement (88.9% category / 94.4% domain) is reported as a caveat, not proof. Two experiments were cut after measurement: a sharper category prompt that regressed accuracy (79.0% → 76.7%, reverted), and BM25 lexical grounding (+1.9% category, +0.0% domain — not worth the retrieval complexity). See `CHANGELOG.md`.
