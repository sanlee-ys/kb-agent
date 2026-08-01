# ADR-011: Update the index incrementally by default; `--rebuild` is the escape hatch

**Status:** Accepted
**Date:** Incremental re-index 2026-07-17 (`0e40dd2`, #45). Recorded as an ADR 2026-07-18, inside [ADR-007](ADR-007-stub-protection-and-freshness-manifest.md). Split out into this record on 2026-08-02 by San's ruling on ADR-007's own recommendation.
**Deciders:** San Lee

---

## Note on provenance: why this is its own record

ADR-007 was commissioned as one record on the premise that both halves share a rationale —
protecting hand-authored work from a pipeline that would clobber it. **The source prose does not
support that premise**, and ADR-007 said so in its own text rather than papering over it. The
argument it made, preserved here because it is the argument for this document existing:

`docs/notes/v2-kickoff.md:138` calls them "Two small, independent pieces," and gives them two
different grounds (`docs/notes/v2-kickoff.md:131-136`):

- Stub protection answers **"no staleness signal"** — a stub silently drifts from its source, and
  hand-edits must survive re-ingestion.
- Incremental re-index answers **"wasteful rebuilds"** — "re-embedding every chunk on every index
  run is fine at today's handful of stubs, but it's rebuild-the-world by design."

Nothing in `chroma_db/` is hand-authored; it is generated and git-ignored (`CLAUDE.md:150`,
`.gitignore:1`). So the incremental-index decision cannot be protecting hand-authored work — there
is none in the artifact it governs. The two share a *release train* (the same "keep the KB fresh"
chore track) and a *shape* (safe default, destructive escape hatch), not a rationale.

`decisions/README.md:44-45` already listed them as two separate migration items.

ADR-007's recommendation was to split it into two ADRs — one for stub protection plus the
fingerprint manifest, one for the incremental index — and to let that file become the first of
them, with the reasoning kept separated by decision so a split would be a cut, not a rewrite. San
ruled in favor on **2026-08-02** and the cut was made the same day. Nothing below is new reasoning.

## Context

kb-agent is a one-directional pipeline: `projects.yaml → ingest.py → kb/*.md → index.py →
chroma_db/ → tools.search_kb → agent` (`CLAUDE.md:67`). Two stages in it are regeneration steps,
and both were originally destructive-by-default in the sense that re-running them would either
throw away work or redo it wholesale. This record covers the second of them; the first is
[ADR-007](ADR-007-stub-protection-and-freshness-manifest.md).

**Stage 2, `index.py`.** The original implementation dropped the ChromaDB collection and re-embedded
everything on every run. That is trivially correct — no stale chunk can survive a drop — and it was
cheap at the current corpus size. It was still "rebuild-the-world by design"
(`docs/notes/v2-kickoff.md:135-136`). #45 replaced the default while keeping the old behavior
reachable.

The author was explicit that this whole track is **plumbing, not the v2 milestone**: "this track has
nothing to measure — it's plumbing... it fixes a real bug but produces no eval, so it doesn't carry
a 'here are the numbers' story" (`docs/notes/v2-kickoff.md:160-163`).

## Decision

**`index.py` updates incrementally by default; `--rebuild` is the escape hatch.** It diffs the
freshly-collected chunks against the persisted collection and re-embeds only new/changed chunks
while deleting chunks from removed or renamed files (`scripts/index.py:245-266`,
`scripts/index.py:323-336`). No second manifest: **the collection itself is the record of what was
indexed last run** (`CLAUDE.md:86-88`). The result is "identical to a full rebuild without
re-embedding everything" (`scripts/index.py:13-16`). `--rebuild` drops and re-embeds from scratch
(`scripts/index.py:308-311`).

The invariant the old drop-and-pave guaranteed — no stale chunks — is preserved by the delete half
of the diff and is covered by a real ChromaDB round-trip test
(`docs/notes/v2-kickoff.md:155-157`; `tests/test_kb_roundtrip.py`, the `@pytest.mark.integration`
test described at `CLAUDE.md:49-53`). The CVE-2026-45829 assessment was re-checked against the new
path and still holds: same embedded `PersistentClient`, same local-only writer, no custom
`embedding_function` (`docs/notes/v2-kickoff.md:157-158`, echoed at `scripts/index.py:300`).

## Downstream surfaces

- **`CLAUDE.md` Architecture §2** — the operative instruction for agents lives there and
  stays there. This ADR carries the why; `CLAUDE.md` carries the do-this. Unmodified by this record.
- **`CLAUDE.md` Commands block** (`CLAUDE.md:23-27`) — the two flags this ADR governs (plain
  `index.py`, `--rebuild`) are documented as part of the pipeline's public surface. Any change here
  must update that block.
- **`docs/notes/v2-kickoff.md` "Near-term chore: keep the KB fresh"** — the origin prose, both
  pieces marked SHIPPED. Retained as the investigation record; this ADR is the decision record.
- **`decisions/README.md`** — its index table carries a row for this ADR, added by the 2026-08-02
  split; its "Still to migrate" note names both items (lines 44-45).
- **[ADR-007](ADR-007-stub-protection-and-freshness-manifest.md)** — the other half of the original
  record. Same release train and same shape, different rationale; see the provenance note above.
- **`docs/notes/chromadb-cve-2026-45829-assessment.md`** — re-checked against the incremental path
  and still holding. Any future change to how `index.py` constructs its client re-opens it.
- **CI (`.github/workflows/ci.yml`)** — runs the integration round-trip test that guards the
  incremental path's no-stale-chunks invariant.

## Consequences

- **Incremental indexing trades trivial correctness for a diff that must be right.** Drop-and-pave
  could not leave a stale chunk; the diff can, if the delete half is wrong. That risk is why the
  round-trip test exists, and it is now load-bearing rather than nice-to-have.
- **The stated benefit is not yet measured.** The record claims the incremental path is faster and
  identical in result; the "identical" half is tested, the "faster" half is asserted, not
  benchmarked. At the current corpus size — a handful of stubs — the win is negligible by the
  author's own account. This is a decision made for how the pipeline will behave later, not for a
  measured gain today.
- **No eval, by design.** Per `docs/notes/v2-kickoff.md:160-163` this track produces no numbers and
  must not be confused with the v2 retrieval-quality milestone.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Keep drop-and-rebuild as the only index path | Correct but "rebuild-the-world by design"; re-embedding every chunk on every run does not scale past the current handful of stubs (`docs/notes/v2-kickoff.md:135-136`) |
| Remove `--rebuild` once incremental works | Kept deliberately as "the escape hatch" (`CLAUDE.md:90`) — the one path that cannot inherit a bug in the diff |
| Track indexed state in a second sidecar manifest, mirroring ingest's | Rejected in favor of using the persisted collection itself as the record of the last run — "no separate manifest" (`CLAUDE.md:87-88`, `docs/notes/v2-kickoff.md:151-152`). Avoids a second file that can disagree with the artifact it describes |
| Filter the `notes` corpus noise (`CLAUDE.md`/`README.md`/`graphify-out/` picked up by the rglob) while touching the index | Named as "a deliberate non-fix" — whether that noise hurts retrieval is a question for the gold set to answer with a number rather than a vibe (`docs/notes/v2-kickoff.md:114-118`). **[Update 2026-08-01: no longer deferred. `is_note_scaffolding()` now excludes scaffolding filenames and `graphify-out/` from the notes sweep, and the gold set answered with the number it was asked for — unfiltered recall@1 0.630 → 0.741, MRR 0.744 → 0.807, one miss recovered and none regressed (#71).]** |
