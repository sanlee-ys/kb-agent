# ADR-002: Adopt the tool-seam threat model, and keep it in kb-agent's own namespace

**Status:** Accepted
**Date:** 2026-07-02 (recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

`kb-agent` exposes four tools to the model (`agent/tools.py`): `search_kb` and
`list_projects` are local; `classify_snippet` and `search_notes` are cross-service HTTP
seams. Everything those tools return re-enters the model's context as part of what it reads
as "the conversation" — and much of that content is attacker-reachable (KB chunks generated
from third-party READMEs, free-form notes, classifier output).

Phase 1 of the "security on the agent's tool seam" work produced
[`docs/notes/tool-seam-threat-model.md`](../docs/notes/tool-seam-threat-model.md), verified
against `agent/tools.py` and `agent/agent.py` on **2026-07-02** — the note states explicitly
that every claim in it was read out of real source, not a summary. It instantiates the house
security posture (`system/SYS-010`, rules 2–4) for kb-agent specifically, and numbers the
attack classes so a Phase 2 gold set can reference them.

That note carries three decisions in prose. This ADR moves them onto a shelf. **Nothing here
is new reasoning.**

**The namespace decision, which is the heart of this record.** The note opens with an
explicit blockquote explaining why it did not take a `SYS` number:

> **Why this lives here and not as a `SYS-NNN`.** `SYS-NNN` is the architecture repo's
> decision-log namespace (`architecture/decisions/`, e.g. the `SYS-003` observation contract
> and `SYS-010` security posture this file cites). A kb-agent-local threat model shouldn't
> squat that shared numbering, so it lands in kb-agent's own `docs/notes/` convention (plain
> descriptive name, like `v2-kickoff.md`). If this is ever promoted to a house-level
> decision, that's a new `SYS-NNN` in the architecture repo that links back here — not a
> renumber of this file.

This is the two-tier rule of `system/SYS-001` being applied correctly **from below**: a
repo-local author declining to claim shared numbering, unprompted. The only thing wrong with
it was the destination, and that was not the author's fault — `kb-agent` had no `decisions/`
tier at all until 2026-07-18, so a repo-local decision had nowhere to land but the notes
folder.

## Decision

**1. Adopt the threat model in `docs/notes/tool-seam-threat-model.md` as kb-agent's canonical
reasoning about its tool seam**, at the repo-local tier. Its scoping calls, quoted from the
note's own "Decision: in/out of scope for the artifact" section:

- **In scope for the Phase 2 gold set:** T1–T6 as the attack classes — T1 direct instruction
  override, T2 authority/roleplay framing, T3 tool-call baiting, T4 citation poisoning, T5
  field smuggling, T6 obfuscated payloads.
- **T7 (resource exhaustion) as a smaller, separate check**, not a full attack class with many
  variants — because `MAX_TOOL_ITERATIONS = 10` already bounds it.
- **Out of scope for v1:** model-level jailbreaking (attacking Claude itself rather than the
  seam), attacking notes-api/classifier input validation directly (their own systems, their
  own threat models), and supply-chain attacks on dependencies.

**2. Record the severity call honestly.** Given **no write tool** and a **config-derived,
SSRF-validated, loopback-pinned host**, the note's finding is that the realistic worst case
today is **answer manipulation and citation poisoning (T4)** — not exfiltration, not
destructive action. The note is explicit that this containment "belongs in the eventual
writeup as the 'good news' an alarmist framing would skip." The stated revisit triggers are
`SYS-010`'s: **if `KB_ALLOWED_HOSTS` is widened, or a write-capable tool is added**,
exfiltration and downstream integrity re-enter scope and the model needs re-scoping.

**3. Keep the note where it is; do not renumber it, and do not promote it.** Per this repo's
`decisions/README.md` split, `docs/notes/` is for analysis and investigation, `decisions/` is
for choices that foreclose an alternative. A threat model is analysis. This ADR records the
decisions the analysis reached; the analysis itself stays in `docs/notes/`.

### Relationship to `system/SYS-016`

[`SYS-016`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-016-agent-tool-seam-threat-model.md)
(**Accepted 2026-07-15**, thirteen days after this note was verified) is a threat model of the
same seam at the system tier. **They are not duplicates, and neither supersedes the other:**

| | `kb-agent/ADR-002` (this note) | `system/SYS-016` |
|---|---|---|
| Deployment assumed | The system as it **actually is** — single-user, loopback-only | The system **projected into** a hypothetical regulated enterprise deployment |
| Framework | Injection-shape taxonomy, grounded in read source | OWASP Top 10 for LLM Applications + STRIDE on the HTTP seams |
| Purpose | Generate a Phase 2 attack gold set — concrete tests | Design reasoning + a controls roadmap; explicitly "write, don't build" |
| Status of controls named | Verified present in source on 2026-07-02 | Six of eight controlled today; T5/T7 are unbuilt roadmap |

Where they touch, they agree. `SYS-016`'s T1 (prompt injection) is the entire subject of this
note, decomposed into six testable shapes; `SYS-016`'s T2 (excessive agency / SSRF) rests on
the same `_validate_endpoint` guard this note verified. The overlap is a system-tier summary
sitting on top of a repo-tier decomposition — the intended shape of the two-tier practice.

**Two honest gaps, both real:**

- **The T-numbers collide, and mean different things in each document.** Both use `T1`–`T7`.
  Only `T1` roughly corresponds (prompt injection / direct instruction override). Everything
  after diverges: this note's `T4` is citation poisoning, `SYS-016`'s `T4` is insecure output
  handling; this note's `T5` is field smuggling, `SYS-016`'s `T5` is a multi-tenant data
  boundary breach; this note's `T7` is resource exhaustion, `SYS-016`'s `T7` is
  repudiation/audit trail. **Any cross-document reference to a bare `T`-number is ambiguous
  and will be misread.** Cite them tier-prefixed — `ADR-002/T4`, `SYS-016/T4` — the same way
  `decisions/README.md` already requires for `SYS`/`ADR` numbers themselves.
- **`SYS-016` does not link back to this note.** The blockquote above predicted that a
  house-level promotion would "link back here." `SYS-016` cites `kb-agent/agent/tools.py`
  directly but contains no reference to `docs/notes/tool-seam-threat-model.md` (verified
  2026-07-18). The prediction was sound; the follow-through did not happen.

## Downstream surfaces

- **[`docs/notes/tool-seam-threat-model.md`](../docs/notes/tool-seam-threat-model.md)** — the
  source of this record, and still the canonical *analysis*. Unchanged and not renumbered.
  Should gain a pointer to this ADR.
- **`decisions/README.md`** — the index table needs an `ADR-002` row. Note that its "Why this
  tier was missing" section already cites this note by name as the example of "the two-tier
  rule being applied correctly from below," so that passage now has an ADR to point at.
- **`system/SYS-016`** — unaffected in substance, but should gain a cross-link to this ADR and
  a note on the colliding `T`-numbering. Filing that is a fast-follow in the architecture
  repo, not this PR (`SYS-009` cascade).
- **`system/SYS-010`** — unchanged and still canonical for the house posture. This ADR
  instantiates its rules 2–4 for kb-agent; it does not amend them.
- **`agent/tools.py`, near `_is_allowed_host` / `KB_ALLOWED_HOSTS`** — the note asks for "a
  one-line comment pointing back here," since widening that allowlist is a revisit trigger. A
  comment flagging the risk exists (`SYS-016` quotes it); a pointer to the threat model does
  not. Open.
- **Phase 2 attack gold set** — does not exist yet. When built, it references T1–T6 from this
  ADR's numbering.
- **`CLAUDE.md`** — unchanged. It carries no tool-seam security rule today, so there is no
  operative instruction to leave in place.

## Consequences

- **The attack classes are now numbered on a shelf**, so the Phase 2 gold set has a stable
  contract to reference instead of chasing a heading in a notes file.
- **The containment finding is recorded as a bound, not a boast.** "No write tool + SSRF-
  validated loopback host" is what makes T3 and T7 low-yield, and it is written down next to
  the exact conditions that would erase it.
- **What it costs:** the model was verified on 2026-07-02 and is a snapshot, not a live
  contract. It has already drifted in two ways (below). It re-earns its accuracy only when
  someone re-reads the source.
- **What it forecloses:** nothing structural. Adding a write-capable tool or widening
  `KB_ALLOWED_HOSTS` is still allowed — it just obligates a re-scope of this model first.

### Verified drift since 2026-07-02 (checked 2026-07-18)

Recorded rather than silently corrected, because the note is a dated snapshot and the ADR
should not rewrite it:

- **Every mechanism the note relies on still exists**, and the load-bearing claims still hold:
  `_is_allowed_host` and `_validate_endpoint` are present in `agent/tools.py`;
  `MAX_TOOL_ITERATIONS = 10` is present in `agent/agent.py`; and `tool_choice` appears
  **nowhere** in `agent/` or `app.py`, so the "never forced, defaults to `auto`" claim is
  still true.
- **The line numbers in the note are stale.** It cites `_is_allowed_host` at
  `tools.py:204–222`, `_validate_endpoint` at `tools.py:225–260`, and `MAX_TOOL_ITERATIONS` at
  `agent.py:34`; they now sit at `tools.py:231`, `tools.py:257`, and `agent.py:47`. Symbol
  names, not line numbers, are the durable reference.
- **The mitigation inventory is missing one control.** `agent/agent.py` now converts successful
  `search_kb` observations into Anthropic **`search_result` content blocks** (`agent.py:124`,
  `agent.py:288`) so the model cites sources. `SYS-016` credits this as part of its T1 control;
  the note, written earlier, does not mention it. Any Phase 2 test of T1/T4/T5 is testing an
  agent with that presentation in place.

## Alternatives Considered

Rejected options as the source note itself frames them, plus the placement options this
recording had to choose between.

| Option | Reason Not Chosen |
|--------|-------------------|
| Give the threat model a `SYS-NNN` | The note's own stated reason: `SYS-NNN` is the architecture repo's shared decision-log namespace, and a kb-agent-local threat model "shouldn't squat that shared numbering." A system-tier version was later written separately as `SYS-016` — which is exactly the promotion path the note described, not a renumber of it |
| Renumber or move the note into `decisions/` now that the tier exists | `docs/notes/` is for analysis; `decisions/` is for choices that foreclose alternatives (`decisions/README.md`). A threat model is analysis. Moving it would also break the note's dated-snapshot integrity and any existing references to its path |
| Treat `SYS-016` as superseding this and record nothing repo-local | They assume different deployments — this one the system as it is, `SYS-016` a hypothetical regulated one — and only this one is verified line-by-line against real source. Deleting the repo-tier record would drop the evidence base the Phase 2 gold set is built from |
| List `MAX_TOOL_ITERATIONS` as an open gap (the earlier draft's position) | Factually wrong, and the note says so explicitly: an earlier draft claimed "no apparent rate limit — structural gap," and re-reading `agent/agent.py` found the cap exists. Corrected before adoption; T7 is bounded, not open |
| Include model-level jailbreaking in the v1 scope | Out of scope by the note's own scoping call: that attacks Claude itself rather than the tool seam, which is the subject under study |
| Include notes-api / classifier input validation in scope | Each upstream service "owns its own threat model"; testing their validation through kb-agent would conflate two boundaries |
| Renumber this note's T1–T7 to avoid the `SYS-016` collision | Not chosen here, but not firmly rejected either — **the record does not preserve a decision on this**, because the collision post-dates the note by thirteen days and appears not to have been noticed. Flagged above; the interim mitigation is tier-prefixed citation. A future ADR may settle it |
