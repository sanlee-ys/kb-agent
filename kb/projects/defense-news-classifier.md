# defense-news-classifier

An NLP pipeline that classifies public, defense-related news snippets into two labels: a **category** (what the article is about — procurement, operations, policy, technology, or industry) and an **operational domain** (air, land, sea, cyber, space, or multi). It uses a single Anthropic API call per article with tool use to force schema-validated structured output, and is built and evaluated entirely on synthetic, publicly safe data. Measured results: operational domain at 97.3% accuracy (0.973 macro-F1) and category at 79.0% accuracy (0.765 macro-F1).

## Tech stack

- **anthropic** — calls the Anthropic API (`claude-sonnet-4-6`) with [tool use](https://docs.anthropic.com/en/docs/tool-use) to classify each article, forcing structured JSON output validated against the label schema at the API layer. Also used to generate the 300-article synthetic dataset.
- **pandas** — handles the labeled dataset (`synthetic_articles.csv`), predictions, and eval outputs (confusion matrices, per-label metrics, misclassification logs).
- **uv** — dependency management and reproducible runs via `uv.lock` (`requirements.txt` kept in sync as a pip fallback).
- **Python 3.11+** — runtime.

## Notes

_Placeholder — author to add design decisions here (e.g. why classifier/evaluator separation, the same-model eval caveat, and the prompt-tuning regression documented in `CHANGELOG.md`)._
