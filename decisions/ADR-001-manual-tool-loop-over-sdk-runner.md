# ADR-001: Keep the manual tool-use loop; reject the SDK's `tool_runner`

**Status:** Accepted
**Date:** 2026-07-11 (recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

`kb-agent` runs its own tool-use loop: each tool is a plain Python function, the JSON schemas
the model sees live explicitly in `TOOLS`, and `execute_tool()` dispatches a tool-use request
to the right function (`agent/tools.py`, `agent/agent.py`).

The Anthropic SDK offers an alternative — `client.beta.messages.tool_runner` with
`@beta_tool`-decorated functions — which generates schemas from type hints and runs the loop
for you. The pitch is less code and less boilerplate. That is a real claim worth testing
rather than dismissing, so it was tested.

A working spike was built on **2026-07-11 against `anthropic` 0.116.0**: the alternative was
implemented for real, not estimated.

**This ADR records a decision that was already made and already written down.** It lived in
the `agent/tools.py` module docstring, complete with date, measured grounds, and a revisit
trigger (commit `fe680e1`, *"Record tool_runner spike rejection in tools.py docstring"*). A
two-tier decision-log audit on 2026-07-18 found this repo had no `decisions/` folder at all,
so a well-formed decision record had nowhere to live but a docstring. Nothing here is new
reasoning — it is the same content, moved to the shelf it should have had.

## Decision

**Keep the manual tool-use loop. Do not adopt `tool_runner`.** Schemas stay explicit in
`TOOLS`; `execute_tool()` stays the dispatch point.

Two measured grounds, both from the spike:

1. **The "less code" premise did not hold.** Net line count went *up*: roughly 25 lines of
   loop removed, roughly 40 lines of wrapper added.
2. **The decorator's auto-generated schema silently dropped a constraint.** `search_kb`'s
   `kind` parameter lost its enum (`["projects", "libraries", "notes"]`). Every safe fix
   either passes an explicit `input_schema` override — defeating the decorator's entire
   purpose — or triples the number of places that enum is duplicated, against this repo's
   single-source-of-truth convention.

The second is the disqualifying one. A silently weakened schema is worse than boilerplate: it
widens what the model may send, and it fails quietly.

**Revisit trigger.** Reopen only if the SDK lets you supply an existing schema dict plus a
description without duplication — i.e. once auto-generation and single-source-of-truth stop
being in tension. Not on general "the SDK improved."

## Downstream surfaces

- `agent/tools.py` module docstring — the original home of this record. Retained as an inline
  summary; this ADR is now the canonical version and the docstring points here.
- `CLAUDE.md` Architecture §4 (manual tool-use loop over the SDK runner) — the operative
  instruction stays there; this ADR carries the reasoning.
- [`system/SYS-003`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-003-agent-tool-layer-contract.md)
  governs the observation envelope these tools return. Unaffected — this decision is about
  loop mechanics, not the contract.

## Consequences

- **The loop stays readable.** `agent.py`'s tool-use loop can be followed top to bottom
  without knowing SDK beta internals, which matters in a repo whose purpose is partly to
  demonstrate understanding of the loop.
- **No beta surface in the critical path.** `tool_runner` is beta; the manual loop depends
  only on the stable Messages API.
- **The cost is real and accepted:** the dispatch boilerplate and the hand-written schemas are
  maintained by hand, and a new tool means editing two places.
- **The enum finding generalises.** Auto-generated schemas are only as good as what the
  generator can see in a type hint. Anywhere a constraint lives outside the type system, a
  generator will drop it silently — worth remembering before adopting any schema-from-code
  tooling.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Adopt `tool_runner` with `@beta_tool` | Measured: net +15 lines, and it silently dropped `search_kb`'s `kind` enum. Both grounds came from a working spike, not from reading the docs |
| Adopt `tool_runner` with explicit `input_schema` overrides | Removes the silent-schema-loss problem by removing the reason to use the decorator at all — you write the schema anyway and still take a beta dependency |
| Adopt it and duplicate the enum in the type hint | Triples the places one constraint is written, against the single-source-of-truth convention this repo holds elsewhere. A drift bug waiting to happen |
| Defer the decision and revisit "later" | The spike was already built; leaving it unrecorded meant re-litigating it from scratch on the next SDK release. Recording the rejection *with its revisit trigger* is what makes "later" actionable |
