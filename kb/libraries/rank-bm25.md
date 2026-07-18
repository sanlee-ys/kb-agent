# rank-bm25

A pure-Python library implementing the BM25 family of ranking algorithms for scoring documents against a query, commonly used for text search and information retrieval.

## What it's for
- Lightweight, dependency-free document ranking/search when a full search engine (e.g., Elasticsearch, Solr) is overkill.
- Building simple keyword-based retrieval systems or prototypes.
- Supports several BM25 variants (e.g., `BM25Okapi`, `BM25L`, `BM25Plus`) so you can experiment with different scoring formulas.
- Often used as a baseline or hybrid component alongside dense/embedding-based retrieval in search and RAG (retrieval-augmented generation) pipelines.

## Gotchas
- It expects pre-tokenized input (lists of tokens per document/query) — you must handle tokenization, lowercasing, and stopword removal yourself; it does no text preprocessing.
- Being pure Python, it can be slow on large corpora since it's not optimized like C-based or inverted-index search engines — fine for small-to-medium datasets, but may not scale well for very large document collections.
