# kb-agent decisions (`ADR-NNN`)

Repo-local decision records for `kb-agent`, per the two-tier practice in
[`system/SYS-001`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-001-record-architecture-decisions.md):
cross-repo decisions get a `SYS-NNN` in the architecture repo, repo-local ones live here.

| # | Title | Status |
|---|-------|--------|
| [ADR-001](ADR-001-manual-tool-loop-over-sdk-runner.md) | Keep the manual tool-use loop; reject the SDK's `tool_runner` | Accepted |
| [ADR-002](ADR-002-agent-tool-seam-threat-model.md) | Adopt the tool-seam threat model, and keep it in kb-agent's own namespace | Accepted |
| [ADR-003](ADR-003-rescope-v2-inward.md) | Retire the shared retrieval backbone; rescope v2 inward | Accepted |
| [ADR-004](ADR-004-retrieval-gold-set-scope.md) | Scope the retrieval gold set at 27 weighted queries, with no LLM judge | Accepted |
| [ADR-005](ADR-005-accept-chromadb-cve.md) | Accept CVE-2026-45829 (chromadb) as tolerable risk | Accepted |
| [ADR-006](ADR-006-mcp-as-second-transport.md) | Serve MCP as a second transport over the same tools, not a second implementation | Accepted |
| [ADR-007](ADR-007-incremental-index-and-stub-protection.md) | Never overwrite a stub without `--force`; update the index incrementally by default | Accepted |

## Why this tier was missing, and what it is not

`kb-agent` had no `decisions/` folder until 2026-07-18. A two-tier audit of the system's
38 decision documents found it was carrying **at least seven ADR-class decisions** in prose —
in `CLAUDE.md`, in `docs/notes/`, and in one case as a fully-formed decision record living
inside a module docstring (`agent/tools.py`, the `tool_runner` rejection, now
[ADR-001](ADR-001-manual-tool-loop-over-sdk-runner.md)).

**This is not a finding that the repo was undisciplined.** The opposite, mostly. The
decisions were *made* carefully and *written down* — dated, with measured grounds and
revisit triggers. They just had no shelf, so they lodged wherever the author happened to be
typing. `docs/notes/tool-seam-threat-model.md` even contains an explicit note explaining why
it deliberately did *not* take a `SYS` number, which is the two-tier rule being applied
correctly from below. What was missing was the local tier it should have landed in instead.

## What goes here vs. elsewhere

| Where | For |
|---|---|
| **`decisions/`** (here) | Choices that foreclose an alternative and bind this repo — architecture, dependencies, protocol design, rejected approaches |
| **`docs/notes/`** | Analysis and investigation that is not itself a decision — threat models, assessments, spikes, kickoff scoping |
| **`CLAUDE.md`** | The operative instruction an agent must follow. A decision recorded here should leave its *rule* in `CLAUDE.md` and cross-link — the ADR is the "why," `CLAUDE.md` is the "do this" |
| **architecture `decisions/`** | Anything binding two or more repos, subject to `SYS-001`'s promotion bar |

## Still to migrate

Recorded so the remainder is a list rather than a vague intention. Each of these is a real
decision currently living in prose:

**Nothing left — the backlog is empty as of 2026-07-18.** All six entries originally listed
here have an ADR: rescoping v2 inward → [ADR-003](ADR-003-rescope-v2-inward.md); the gold-set
scope → [ADR-004](ADR-004-retrieval-gold-set-scope.md); the chromadb CVE acceptance →
[ADR-005](ADR-005-accept-chromadb-cve.md); MCP as a second transport →
[ADR-006](ADR-006-mcp-as-second-transport.md); and the two `ingest.py`/`index.py` rules
(stub protection, incremental re-index) migrated together as
[ADR-007](ADR-007-incremental-index-and-stub-protection.md), since they are one decision about
not destroying work by default. The tool-seam threat model was migrated in the same pass as
[ADR-002](ADR-002-agent-tool-seam-threat-model.md).

The section stays because "migration completed" is itself worth recording. If a future audit
finds another decision sitting in prose, list it here rather than fixing it silently.

## Conventions

- Identifier and filename are both `ADR-NNN` (`ADR-001-short-title.md`)
- Shape: Context → Decision → Downstream surfaces → Consequences → Alternatives Considered
- Cross-tier references are prefixed so a number is never ambiguous: `system/SYS-003`,
  `kb-agent/ADR-001`, `classifier/ADR-012`
