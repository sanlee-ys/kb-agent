# Test-coverage backfill: close the blind spots, not the number

*A breadcrumb for whoever (human or a fresh Claude session) picks up kb-agent's test
hardening. Measured 2026-06-22. Read alongside `README.md` and `tests/` — and note the
house view already written down in `notes-api/docs/09-coverage.md` and learning-notes
note 20: **coverage is a guide, not a target.***

> **The point is NOT 100%.** Coverage measures whether a line *ran*, not whether a test
> *checked the right thing* — an assertion-free test still scores. Chase the number and you
> end up writing tests for trivial getters, `__main__` guards, and branches that can't
> happen. What coverage is genuinely good for is a **spotlight**: it finds real logic that
> *no test touches at all*. This backfill closes those blind spots and deliberately leaves
> the glue alone.

---

## Measured state (2026-06-22)

`uv run --with pytest-cov pytest --cov=agent --cov=scripts --cov-report=term-missing`
→ **56% overall, 28 tests pass, 0 fail.** (pytest-cov isn't a dev dep by design; pull it in
ephemerally with `--with` rather than adding it.)

| Module | Cover | The gap that actually matters |
|--------|-------|-------------------------------|
| `agent/agent.py` | **0%** | the entire `KBAgent`: the `ask()` tool-use loop, the `MAX_TOOL_ITERATIONS` cap, `_final_text()` |
| `scripts/ingest.py` | 59% | `generate_project_stub` / `generate_library_stub` (the client is **injected** → mockable), `main()`, the missing-`projects.yaml` exit |
| `agent/tools.py` | 70% | `search_kb` happy path + the empty-`kind` hint; `_get_collection` collection-missing branch; `classify_snippet` timeout/`HTTPError` branch; `_project_endpoint` missing-file |
| `scripts/index.py` | 71% | `notes_dirs()` when `projects.yaml` has a `notes_dirs:` list; the notes walk + skip-missing in `collect_documents`; `main()` empty-docs early return |
| `app.py` | excluded | Gradio UI — runtime-only; **leave it** |

## The work (risk-weighted, not line-weighted)

**P1 — `agent/agent.py`, 0% → exercised.** Highest value: it's the heart of the agent and
entirely untested, yet fully testable **offline** — mock `anthropic.Anthropic` so
`.messages.create()` returns canned content blocks (no network, no key; keep the suite's
offline contract).
- `_final_text()` over a stub response → pure logic, trivial.
- `ask()` single-turn: mock a response with `stop_reason="end_turn"`; assert the text comes
  back and the turn lands in `self.messages`.
- `ask()` tool loop: mock a first response carrying a `tool_use` block, then an `end_turn`;
  assert the tool ran and a `tool_result` block was fed back in.
- the `MAX_TOOL_ITERATIONS` cap: mock a response that *always* requests a tool; assert it
  stops at the cap instead of looping forever.

**P4 — the mockable branches in `tools.py` / `index.py` / `ingest.py`.** A cluster of small
tests, all offline:
- `search_kb` happy path via an **ephemeral** store (`chromadb.EphemeralClient()`) with one
  fake doc; plus the `kind=`-filter-empty case asserting the "drop the filter" `next_action`.
- `classify_snippet` timeout: monkeypatch `httpx.post` to raise `httpx.TimeoutException`;
  assert the error observation + retry guidance (the existing test only covers `ConnectError`).
- the stub generators: pass a **mock client** returning a fake text block; assert a stub is
  written. (The current test file *says* these need the live API — they don't; the client is
  a parameter, so inject a fake.)
- the small filesystem guards: `_project_endpoint` / `load_projects` against a missing file
  (a `tmp_path`).

Every new tool result must still pass the `_obs()` SYS-003 grader in `tests/test_tools.py`.

## Explicitly NOT worth doing (the anti-goal)

- `app.py` — Gradio UI; needs the framework + a browser. Excluded on purpose.
- the `__main__` smoke blocks in `tools.py` / `agent.py` and the `if __name__ == "__main__"`
  guards — entry points, not logic.
- **Don't write assertion-light tests just to push 56% upward.** If closing P1 + P4 lands the
  number somewhere in the mid-70s, that's a *byproduct* of covering the logic that matters —
  never the objective. A lower number with real assertions beats a higher one without.

## How to start

1. New session, fresh budget. Read this + `README.md` + `tests/test_tools.py` (it already
   shows the offline-mocking and `_obs()` patterns to copy).
2. Do **P1 first** — biggest blind spot, cleanest mock job.
3. Keep the suite offline (mock the client; ephemeral/`tmp_path` Chroma). One concern per
   branch; CI (ruff + pytest) gates the PR.
