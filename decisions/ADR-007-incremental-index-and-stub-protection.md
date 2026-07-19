# ADR-007: Never overwrite a stub without `--force`; update the index incrementally by default

**Status:** Accepted
**Date:** Stub protection 2026-06-20; `--check`/`--accept` fingerprint manifest 2026-07-16 (`311909f`, #44); incremental re-index 2026-07-17 (`0e40dd2`, #45). Recorded as an ADR 2026-07-18.
**Deciders:** San Lee

---

## Note on scope: this is probably two decisions, not one

This ADR was commissioned as one record on the premise that both halves share a rationale —
protecting hand-authored work from a pipeline that would clobber it. **The source prose does not
support that premise, and it is worth saying so before the reasoning below rather than papering
over it.**

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

`decisions/README.md:44-45` already lists them as two separate migration items.

**Recommendation: split this into two ADRs** — one for stub protection plus the fingerprint
manifest, one for the incremental index — and let this file become the first of them. Both are
recorded here for now so neither is lost, and the reasoning below is kept separated by decision so
a split is a cut, not a rewrite. That call is San's.

## Context

kb-agent is a one-directional pipeline: `projects.yaml → ingest.py → kb/*.md → index.py →
chroma_db/ → tools.search_kb → agent` (`CLAUDE.md:67`). Two stages in it are regeneration steps,
and both were originally destructive-by-default in the sense that re-running them would either
throw away work or redo it wholesale.

**Stage 1, `ingest.py`.** Stubs are LLM-generated first drafts. The intended workflow is that the
author hand-annotates them afterwards, which makes the file more valuable than what the generator
produced. A regeneration pass that overwrites is therefore a data-loss event, not a refresh. The
initial scaffolding (`740b039`, 2026-06-20) already skipped existing files
(`scripts/ingest.py:207`, `write_stub`).

That safety had a cost the author named later: freezing the stub means "once written, a stub is
frozen while the project it describes moves on (a new dependency, a rewritten README)"
(`docs/notes/v2-kickoff.md:126-128`), with **no signal** that drift had happened. The only remedy
on offer was `--force`, which is exactly the destructive act the skip existed to prevent. #44
closed that gap.

**Stage 2, `index.py`.** The original implementation dropped the ChromaDB collection and re-embedded
everything on every run. That is trivially correct — no stale chunk can survive a drop — and it was
cheap at the current corpus size. It was still "rebuild-the-world by design"
(`docs/notes/v2-kickoff.md:135-136`). #45 replaced the default while keeping the old behavior
reachable.

The author was explicit that this whole track is **plumbing, not the v2 milestone**: "this track has
nothing to measure — it's plumbing... it fixes a real bug but produces no eval, so it doesn't carry
a 'here are the numbers' story" (`docs/notes/v2-kickoff.md:160-163`).

## Decision

**1. `ingest.py` never overwrites an existing stub unless `--force` is passed.** Hand-edits to KB
files survive re-ingestion (`CLAUDE.md:73-74`, `scripts/ingest.py:10-11`, `scripts/ingest.py:207`).

**2. Drift is detected out-of-band via a fingerprint manifest, not by overwriting.** Each generated
project stub records a source fingerprint — description + deps + README prefix — in the sidecar
`kb/.ingest-manifest.json` (`scripts/ingest.py:49`, `scripts/ingest.py:213`). `--check` recomputes
it and reports `fresh` / `stale` / `untracked` / `missing` / `skipped` / `unmanaged`, writing
nothing; `--accept` blesses current source as the baseline without regenerating — "the
non-destructive alternative to `--force` for hand-curated stubs" (`CLAUDE.md:79-80`). Both modes are
offline and short-circuit before any model call (`scripts/ingest.py:475-481`). `--check` exits
non-zero on `stale`, so it can gate CI or a pre-commit hook later
(`docs/notes/v2-kickoff.md:144-145`).

Three supporting choices, all stated in the record:

- **The fingerprint hashes exactly the prompt inputs** (the first `README_PROMPT_CHARS = 8000` of the
  README, `scripts/ingest.py:52-55`), so "stale" means the inputs that produced the stub changed —
  not "a byte past what the model ever saw" — and hand-edits to a stub never false-trip it
  (`docs/notes/v2-kickoff.md:147`).
- **The manifest is JSON, not `*.md`,** so `index.py` never embeds it (`CLAUDE.md:80-81`,
  `scripts/ingest.py:48`), and it is **committed** (not in `.gitignore`) so baselines travel across
  machines and sessions (`CLAUDE.md:81-82`).
- **Freshness tracks project stubs only.** Library stubs are generated from a package name, not a
  source file, so there is nothing for them to drift against (`CLAUDE.md:82`).

**3. `index.py` updates incrementally by default; `--rebuild` is the escape hatch.** It diffs the
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

- **`CLAUDE.md` Architecture §1 and §2** — the operative instruction for agents lives there and
  stays there. This ADR carries the why; `CLAUDE.md` carries the do-this. Unmodified by this record.
- **`CLAUDE.md` Commands block** (`CLAUDE.md:23-27`) — the five flags (`--force`, `--check`,
  `--accept`, plain `index.py`, `--rebuild`) are documented as the pipeline's public surface. Any
  change here must update that block.
- **`docs/notes/v2-kickoff.md` "Near-term chore: keep the KB fresh"** — the origin prose, both
  pieces marked SHIPPED. Retained as the investigation record; this ADR is the decision record.
- **`decisions/README.md`** — its "Still to migrate" list names both items (lines 44-45) and its
  index table needs a row for this ADR. **Not updated by this session**: the index is an aggregated
  file and several ADRs are being migrated in parallel, so the wiring belongs to a single integrator
  after the content lands.
- **`kb/.ingest-manifest.json`** — a committed artifact created by this decision. Merge conflicts in
  it are expected across machines; `--accept` regenerates it.
- **`kb/projects/kb-agent.md`** — the known staleness risk (`CLAUDE.md:122-128`). It is hand-written
  and outside the pipeline, so `--check` reports it `unmanaged` rather than fresh or stale. This
  decision makes the risk *visible*; it does not fix it.
- **`docs/notes/chromadb-cve-2026-45829-assessment.md`** — re-checked against the incremental path
  and still holding. Any future change to how `index.py` constructs its client re-opens it.
- **CI (`.github/workflows/ci.yml`)** — runs the integration round-trip test that guards the
  incremental path's no-stale-chunks invariant. `--check` is *not* wired into CI today; the
  non-zero exit is a capability, not an active gate.

## Consequences

- **Hand-annotation is safe by default.** Re-running `ingest.py` on the whole of `projects.yaml`
  costs nothing and destroys nothing, which is what makes the pipeline re-runnable at all.
- **Drift became visible without becoming destructive.** Before #44 the only responses to a drifted
  stub were "notice it by accident" or "`--force` and lose the edits." `--check`/`--accept` add a
  third: see it, judge it, bless it.
- **A first run on any machine reports every stub `untracked`** until `--accept` or a `--force`
  regenerate establishes baselines (`docs/notes/v2-kickoff.md:148-149`,
  `scripts/ingest.py:319`). This bit the author for real — `v2-kickoff.md:108-109` records the
  manifest missing entirely at the start of the v2 gold-set work, making a `--accept` pass part of
  step 0 before any labeling.
- **`--accept` can bless drift.** It is the non-destructive option precisely because it does not
  look at the stub, only the source. Accepting a stale stub silences the signal without fixing the
  content; the report says "review, then `--accept` or `--force`" (`scripts/ingest.py:321`) and the
  review is on the human.
- **The manifest is a committed file that changes on most ingests,** so it is a small, predictable
  merge hotspot across San's two machines. Chosen deliberately over git-ignoring it, so baselines
  are shared rather than per-clone.
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
| Overwrite stubs on every ingest (no `--force` gate) | Destroys hand-annotations, which are the point of the stubs. `scripts/ingest.py:10-11`: "Stubs are NEVER overwritten once they exist, so hand-annotations you add later are preserved" |
| Keep the `--force`-only world; skip `--check`/`--accept` | Leaves the staleness bug the record names: a stub is "frozen while the project it describes moves on" with no signal, and the only remedy is the destructive one (`docs/notes/v2-kickoff.md:126-134`) |
| Make `--check` regenerate or auto-fix drifted stubs | Would re-introduce the clobber it exists to avoid. `--accept` is explicitly framed as "the non-destructive way to bless the existing hand-curated stubs, unlike `--force`" (`docs/notes/v2-kickoff.md:145-147`) |
| Fingerprint the whole README rather than the prompt prefix | The fingerprint hashes exactly the 8000-char prompt input so "stale" means the generator's inputs changed, not that a byte past what the model ever saw moved (`scripts/ingest.py:52-55`) |
| Fingerprint the stub file's own contents | Would make every hand-edit trip the check — the exact false positive the input-side fingerprint avoids (`docs/notes/v2-kickoff.md:147`) |
| Track freshness for library stubs too | Library stubs are generated from a package name, not a source file, so they have nothing to drift against (`CLAUDE.md:81-82`) |
| Git-ignore the manifest as a local cache | Baselines would not travel; every machine and fresh clone would report everything `untracked`. Committed instead, accepting the merge-hotspot cost (`CLAUDE.md:81-82`) |
| Store the manifest as Markdown alongside the stubs | `index.py` embeds every `kb/**/*.md`, so a Markdown manifest would be indexed and become retrievable noise. JSON keeps it out of the corpus (`scripts/ingest.py:48`) |
| Keep drop-and-rebuild as the only index path | Correct but "rebuild-the-world by design"; re-embedding every chunk on every run does not scale past the current handful of stubs (`docs/notes/v2-kickoff.md:135-136`) |
| Remove `--rebuild` once incremental works | Kept deliberately as "the escape hatch" (`CLAUDE.md:90`) — the one path that cannot inherit a bug in the diff |
| Track indexed state in a second sidecar manifest, mirroring ingest's | Rejected in favor of using the persisted collection itself as the record of the last run — "no separate manifest" (`CLAUDE.md:87-88`, `docs/notes/v2-kickoff.md:151-152`). Avoids a second file that can disagree with the artifact it describes |
| Filter the `notes` corpus noise (`CLAUDE.md`/`README.md`/`graphify-out/` picked up by the rglob) while touching the index | Named as "a deliberate non-fix" — whether that noise hurts retrieval is a question for the gold set to answer with a number rather than a vibe (`docs/notes/v2-kickoff.md:114-118`) |
