# ADR-005: Accept CVE-2026-45829 (chromadb) as tolerable risk

**Status:** Accepted
**Date:** 2026-07-04 (amended 2026-07-17; recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

Dependabot raised a **critical** alert against `chromadb`:
[GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) /
[CVE-2026-45829](https://nvd.nist.gov/vuln/detail/CVE-2026-45829), CVSS v4 9.3. Per the
upstream report ([chroma-core/chroma#6717](https://github.com/chroma-core/chroma/issues/6717))
it is two related code-injection paths:

1. **Server-mode RCE.** An unauthenticated attacker hits the ChromaDB HTTP server's
   `/api/v2/tenants/{tenant}/databases/{db}/collections` endpoint with a malicious model
   repository and `trust_remote_code=true`, achieving code execution on the server.
2. **Client-side RCE via a poisoned collection.** If an attacker can write to a collection in
   advance, a client that later interacts with it normally can trigger code execution through
   the embedding-function configuration retrieval mechanism.

At the time of assessment no patched release existed upstream: the version kb-agent resolves
to, `1.5.9`, was simultaneously the latest release and the top of the vulnerable range. So
"upgrade" was not on the table — the only choices were to assess, to remove the dependency,
or to leave a critical flag sitting open with no explanation.

The assessment was made **against the real code, not assumed** (`agent/tools.py`,
`scripts/index.py`), and written up in
[`docs/notes/chromadb-cve-2026-45829-assessment.md`](../docs/notes/chromadb-cve-2026-45829-assessment.md)
on 2026-07-04 (PR #25). It was revisited on 2026-07-17 when PR #45 introduced the incremental
re-index path, and re-confirmed to hold there too.

**This ADR records a decision that was already made and already written down.** The
2026-07-18 two-tier decision-log audit listed it as one of the ADR-class decisions living in
prose (see [`decisions/README.md`](README.md), "Still to migrate"). Nothing here is new
reasoning — it is the assessment's own reasoning moved onto the shelf it belongs on. The
analysis note stays where it is; it is the long-form evidence this record points at.

## Decision

**Accept CVE-2026-45829 as tolerable risk. Dismiss the Dependabot alert with a link back to
the assessment note rather than leaving it open and unexplained.** No code change, no pin
change, no dependency removal.

The acceptance rests on a specific threat model — four conditions in the current code, each
of which independently breaks one of the two exploit paths:

- **No server mode.** Every chromadb use here is `chromadb.PersistentClient(path=...)` — an
  embedded, in-process, file-backed client (`agent/tools.py:116`, `scripts/index.py:303`).
  kb-agent never starts the ChromaDB HTTP server, so path 1's endpoint does not exist to
  attack.
- **`trust_remote_code` is never set.** Grepped the whole repo; the flag path 1 requires does
  not appear anywhere.
- **No attacker has write access to the collection.** `scripts/index.py`'s `main()` is the
  only writer of the `knowledge_base` collection, and it writes exclusively from `kb/**/*.md`
  — files this same pipeline generates locally from the user's own project manifests, plus
  the user's own notes. `chroma_db/` is local, gitignored (`.gitignore:1`), and never shared
  or network-exposed, so there is no external party who could poison the collection in
  advance for path 2.
- **No custom embedding-function config.** `create_collection()` is called with no
  `embedding_function` argument, so there is no serialized config for path 2's retrieval
  mechanism to deserialize in the first place; chromadb's own local default
  (`all-MiniLM-L6-v2`) is used.

The 2026-07-17 amendment confirmed this holds under **both** index paths: `--rebuild`
(`delete_collection` then `create_collection`) and the default incremental path
(`get_or_create_collection`, then `upsert`/`delete`). The incremental path persists the
collection across runs but adds no new writer and no new source.

### Revisit trigger (prominent by design — this acceptance expires on any of these)

Re-run the check if **any** of the following becomes true:

- kb-agent starts running ChromaDB in server mode (`HttpClient`, or hosting the ChromaDB
  server itself) instead of `PersistentClient`.
- Any code path sets `trust_remote_code=True`.
- `chroma_db/` is ever shared, synced, or populated from a source kb-agent does not fully
  control (e.g. ingesting a collection built by another machine or user).
- **A patched chromadb version ships — bump to it regardless of the above**, since a real fix
  is strictly better than an assessed-safe workaround.

The first three are exactly the four conditions above, inverted. This is a *conditional*
acceptance, not a judgement that the CVE is unimportant.

## Downstream surfaces

- [`docs/notes/chromadb-cve-2026-45829-assessment.md`](../docs/notes/chromadb-cve-2026-45829-assessment.md)
  — the long-form evidence. Stays as-is; this ADR is the decision record that points at it.
- `scripts/index.py:295-302` — an in-code comment naming the load-bearing conditions and
  telling the next editor to re-read the assessment before changing how the client is opened
  or the collection is populated. That comment *is* the revisit trigger at the point of
  change; keep it in sync with the list above.
- `agent/tools.py:111-113` — the same guard comment on the read-side client.
- `kb/projects/kb-agent.md:17` — the self-description stub records chromadb as
  "local, embedded (`PersistentClient`, never HTTP/server mode)". Hand-maintained and outside
  the ingest pipeline, so it needs a manual edit plus a re-index if this ever changes.
- `docs/notes/v2-kickoff.md:158` — v2 scoping explicitly carries the same conditions forward
  ("same embedded `PersistentClient`, same local-only writer, no custom
  `embedding_function`").
- [`decisions/README.md`](README.md) — index row and the "Still to migrate" list; this entry
  moves from pending to recorded.
- GitHub Dependabot alert state for this advisory — dismissed as tolerable risk with a link
  to the assessment. (Alert state is outside the repo and was not verified while writing
  this ADR.)

## Consequences

- **The alert stops being noise.** A dismissed-with-reasoning alert is auditable; a
  permanently-open critical one trains everyone to ignore the alert list.
- **kb-agent keeps a known-vulnerable dependency in the tree, deliberately.** If the threat
  model changes without anyone noticing, the exposure is real. The in-code comments at both
  client sites are the mitigation for that, and they are only as good as the next editor
  reading them.
- **Four ordinary-looking design choices are now load-bearing security properties.**
  Embedded-only client, no `trust_remote_code`, single local writer, and default embedding
  function stopped being incidental the moment this decision was made. Changing any of them
  is now a security change, not a refactor.
- **The acceptance has a shelf life.** It survives only until a patched chromadb ships, at
  which point the instruction is to upgrade unconditionally rather than re-argue the
  assessment.
- **The method generalises.** Checking an advisory against the actual call sites, rather than
  against the version range alone, is what turned a critical CVSS score into a defensible
  disposition. Version-range matching alone would have forced either a false emergency or an
  unexamined dismissal.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Upgrade to a patched chromadb | Not available. At assessment time `1.5.9` was both the latest release and the top of the vulnerable range, so there was nothing to upgrade *to*. This remains the preferred outcome — the revisit trigger says to take it the moment it exists |
| Pin to an older chromadb release | Does not help: `1.5.9` is the *top* of the vulnerable range, so earlier releases are inside it too. Downgrading trades a known exposure for the same exposure plus lost functionality |
| Leave the Dependabot alert open and unaddressed | Explicitly rejected in the assessment's disposition. An unexplained standing "critical" flag carries no information — a reader cannot tell it from an unassessed one, and it erodes attention to every future alert |
| Drop or replace chromadb | The record does not preserve an evaluation of this. The assessment goes straight from "no patch exists" to assessing exploitability against the real code; no vector-store swap is discussed, so no cost estimate for it can be honestly reported here |
