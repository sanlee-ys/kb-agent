# ADR-008: Adopt the advisory agentic PR review lane (SYS-021 instance)

**Status:** Accepted — **VERIFIED 2026-07-25**: SYS-021 req. 2 met, a live `@claude` run posted a real review on [PR #59](https://github.com/sanlee-ys/kb-agent/pull/59). That run still hit its turn cap (21 turns, $0.59); the prompt was subsequently scoped to the diff to bring the loop down.
**Date:** 2026-07-24
**Deciders:** San Lee

---

## Context

`ci.yml` holds this repo's gates: ruff, pytest (including the real-ChromaDB integration
round-trip), the two outward contract guards (`check_classify_contract.py` for SYS-004,
`check_notes_contract.py` for SYS-006), and the ADR lint. All deterministic, all answering
"did this change break something measurable."

They cannot answer the questions that actually bite this repo, because those are conventions
rather than assertions:

- A new tool returning a bare dict instead of a `_success`/`_problem` **SYS-003 observation**.
  The `_obs()` grader in `tests/test_tools.py` catches this for tools it knows about; a new
  tool wired only into `mcp_server/` would not be covered.
- `mcp_server/server.py` reimplementing a tool or retyping a description instead of consuming
  `agent/tools.py`. **Both transports keep working**, which is exactly why the fork is silent.
- `DEFAULT_MODEL` (`agent/agent.py`) and `MODEL` (`scripts/ingest.py`) drifting apart —
  `CLAUDE.md` says update both together, and nothing enforces it.
- `kb/projects/kb-agent.md` going stale after a tool-layer change. It is hand-written, outside
  `ingest.py`'s pipeline, and `--check` can only report it as `unmanaged`.

Every one is a judgment call, and today the only reviewer making them is the person who wrote
the change. [SYS-021](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-021-agentic-ci-proves-itself-by-artifact.md)
standardised the agentic-review lane after `defense-news-classifier` proved it out (its
[ADR-016](https://github.com/sanlee-ys/defense-news-classifier/blob/main/decisions/016-claude-code-action-pr-review.md)),
including the three ways it can fail silently. This ADR is the second instance.

## Decision

**Adopt `.github/workflows/claude-review.yml` as an advisory lane, conforming to SYS-021's four
requirements. It comments; it never fails the build and never pushes.**

| SYS-021 requirement | How this lane satisfies it |
|---|---|
| 1. Grant write tools explicitly | `--allowedTools` names `Read,Grep,Glob` plus the inline-comment MCP tool and `gh pr comment/diff/view`; the prompt states that posting is the deliverable. **The grant must match what the prompt asks for** — see Consequences. |
| 2. Verify at adoption with a live artifact | **Met 2026-07-25** — a live `@claude` run posted a real review on [PR #59](https://github.com/sanlee-ys/kb-agent/pull/59). See the Status header and the four measured runs in Consequences. |
| 3. Enforce advisory status mechanically | `continue-on-error: true` on the **step**, plus job-level as a backstop. Was job-only until 2026-07-25, which did **not** satisfy this requirement — see the Dependabot correction below and `SYS-021` Amendment 1. |
| 4. Guard the trigger surface | `pull_request` skips forks (fails closed anyway); `issue_comment` gated on `author_association == 'OWNER'` because that event **fails open with secrets**. |

Also carried over from the first instance: `pull_request: [opened]` only (not `synchronize`),
`id-token: write` (the action's OIDC exchange fails without it), model pinned to
`claude-sonnet-5` per SYS-002, `--max-turns 15`, `contents: read`.

The prompt targets the four repo-specific invariants above rather than generic code review.

## Downstream surfaces

| Surface | State |
|---|---|
| `.github/workflows/claude-review.yml` | **New.** The lane. Cannot review its own changes — the action self-skips when the head-ref copy differs from the default branch's — so edits here need human review plus a post-merge `@claude` verification. |
| **`ANTHROPIC_API_KEY` repo secret** | **Set 2026-07-25.** No longer the blocker. |
| [`defense-news-classifier`'s claude-review lane](https://github.com/sanlee-ys/defense-news-classifier/blob/main/.github/workflows/claude-review.yml) | **Has the same narrow tool grant** (no `Read`/`Grep`/`Glob`) while its prompt also references source files. It has only been exercised on small diff-only PRs, so it has not hit the wall yet. Should get the same widening — tracked as a fast-follow, not fixed here, because it is a different repo's PR. |
| `ci.yml` | Unchanged. Separate workflow, separate concurrency group, no interaction. The gates stay deterministic. |
| The review prompt inside the workflow | **Maintained surface.** Names `agent/tools.py`, `mcp_server/server.py`, `agent/agent.py`'s `DEFAULT_MODEL`, `scripts/ingest.py`'s `MODEL`, both contract-guard scripts, and `kb/projects/kb-agent.md`. Update it when any of those move. |
| [SYS-021](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-021-agentic-ci-proves-itself-by-artifact.md) | The standard this instantiates. This ADR is its second instance; the first is the classifier's ADR-016. |
| [ADR-002](ADR-002-agent-tool-seam-threat-model.md) | Requirement 4's `issue_comment` guard is that threat model applied at the CI trigger surface — an untrusted actor reaching a privileged execution context. |
| [ADR-006](ADR-006-mcp-as-second-transport.md) | The "one home for the tool layer" invariant the prompt asks the reviewer to protect is this ADR's core constraint. |

## Consequences

- **A second reader exists where there was none.** This is a solo repo; the value is not that the
  model is a better engineer, it is that it is not the person who just wrote the code.
- **The silent-drift conventions get a reviewer.** The MCP-fork and model-pin-divergence failures
  both keep every test green by construction, so a convention-aware reader is the only instrument
  available short of writing new lint.
- **Cost:** one review per PR opened, Sonnet, turn-capped — measured at ~$0.14 per review on the
  classifier. It does not scale with pushes.
- **This ADR ships in a knowingly unverified state.** SYS-021 req. 2 demands a live artifact at
  adoption and this repo cannot produce one until the secret is set. Recording that as an explicit
  `Status` caveat rather than quietly merging is the point of the requirement — under SYS-021 a
  green pipeline is not evidence, so "CI passed" would not have made this lane working.
- **The prompt's scope, not just the tool grant, drives cost.** Three live runs here:

  | Run | Grant | Result |
  |---|---|---|
  | 1 | no `Read`/`Grep`/`Glob` | 12 denials, 16 turns, $0.50, **nothing posted** |
  | 2 | `+Read,Grep,Glob` | 9 denials, 21 turns, $0.59, **review posted** |
  | 3 | same, diff-scoped prompt | 6 denials, 20 turns, $0.554, **posted, no cap error** |

  **Run 3 underdelivered against its own prediction, and that is the useful finding.** Scoping the
  prompt to the diff was expected to cut the loop substantially; it moved 21→20 turns and
  $0.59→$0.554. What it did buy is completion instead of a max-turns error, which is worth having.
  What it did not buy is cost. The review also still enumerated every invariant it had skipped,
  despite the prompt explicitly saying not to — so the instruction was followed in substance
  (files were not opened without cause) and ignored in form.

  | 4 | same, auto-trigger on a doc-only PR | 8 denials, 14 turns, **$0.367**, posted |

  Run 4 is the **common case** — the automatic `opened` review, not a `@claude` follow-up — and it
  is materially cheaper than runs 2 and 3. Those were comment-triggered on PRs that modified the
  workflow itself, where the comment thread adds context and the diff is the reviewer's own
  configuration. So the working figure is **~$0.37 for a routine review**, with `@claude`
  re-reviews costing more.

  Read together the four runs say loop length is **not** primarily driven by prompt breadth, and
  the residual driver is not visible from here — the action reports `permission_denials_count` and
  never the refused tool names, and denials persisted (8) even on the cheapest run. Further tuning
  would be guessing against something unnameable, so it stops here.

  **A correction, recorded because the wrong version was committed first:** run 1's failure was
  attributed to `--allowedTools` *replacing* the default tool set. The action's docs say the
  opposite — it **extends** the defaults, "the base GitHub tools are always included." The runs
  were not a clean A/B (prompt and diff both differed), so what is actually established is that
  naming the read tools coincided with fewer denials and a successful post, not why. The residual
  9 denials remain **unidentified**: the action reports `permission_denials_count` and never the
  refused tool names, so there is no way to close that gap from the logs.

  Given that, the loop length was attacked from the prompt side instead — the reviewer now reads
  the diff first and checks only the invariants that diff could plausibly break, rather than
  walking a seven-item checklist against every PR. Widening permissions to chase an unnamed
  denial would have been guessing with money.
- **Dependabot PRs are not reviewed. ~~And the run still reports success.~~** The action refuses
  bot-authored PRs by default — *"Workflow initiated by non-human actor: dependabot (type: Bot)"*.
  Observed on [PR #62](https://github.com/sanlee-ys/kb-agent/pull/62) the same night the lane went
  live. **Accepted, not fixed:** a bump is a lockfile and version pins, `ci.yml` is the real gate,
  and reviewing each one costs ~$0.37 for close to no signal. Recorded so the lane's coverage is
  not read as wider than it is; an `allowed_bots` input exists if that changes. Same conclusion and
  same reasoning as the classifier's ADR-016.

  **Correction, 2026-07-25.** The struck clause, and "`continue-on-error: true` turns that exit-1
  step into a green job", were both wrong — and wrong in both repos, since this note was copied
  from ADR-016. `continue-on-error` sat on the **job**, which greens the *workflow run*. The
  *check run* — what the PR shows and `gh pr checks` reads — concluded `failure`. Measured on the
  classifier's PR #123 (run `30141009937`): run `success`, check run `review` **`failure`**. So the
  bumps were not quietly skipped; each wore a red X, which is the exact signal `continue-on-error`
  was adopted to suppress.

  **Fixed here:** `continue-on-error` moved to the action step (job-level kept as a backstop), and
  bot-authored PRs now skip at the `if` so the accepted outcome is a clean skip rather than a
  failed run. The `@claude` owner path stays ungated — asking for a review on a bump is a human
  decision and still works. Generalised in `SYS-021` Amendment 1.
- **`@claude` on a commit does nothing, and cannot be made to.** `commit_comment` is not among the
  action's supported events (`issue_comment`, `pull_request_review_comment`, `issues`,
  `pull_request_review`). Wiring it would start a run that then fails building context — worse
  than not firing. `pull_request_review_comment` is wired instead, so inline-line comments work;
  top-level PR comments already did. **Comment on a PR, open or merged — never on a commit.**
- **Revisit when:** per-review cost climbs materially above the measured ~$0.37, the action starts
  reporting *which* tools it denied (which would make the residual loop cost diagnosable rather
  than guessable), or the lane produces noise — in which case narrow the prompt or remove it
  rather than leave it running unread.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| **Wait for the secret before merging the lane** | The workflow is inert without the secret — it cannot spend, comment, or redden a build — so merging it early costs nothing and keeps the SYS-021 rollout in one piece. The unverified state is recorded in `Status` rather than hidden, which is the honest version of shipping it. |
| **Make it a gate that fails the build** | A non-deterministic reviewer with merge authority turns every model error into a blocked merge. Worse here than on the classifier: SYS-021 explicitly does not authorise promoting an agentic lane to gating, because a silently-muted *gate* is a hole in the build rather than a missing opinion. |
| **Copy the classifier's workflow verbatim** | The mechanics transfer; the prompt does not. A reviewer told to watch for eval-metric drift would find nothing here and miss the MCP-fork and observation-contract failures that actually threaten this repo. |
| **Write lint for the conventions instead** | Better where possible, and genuinely worth doing for the model-pin divergence (two constants, mechanically comparable). But "did `mcp_server/` reimplement a tool" and "is this stub now stale" are not mechanically decidable, which is precisely the class this lane covers. Not mutually exclusive — lint should still be written where a rule is checkable. |
| **`synchronize` as well, so every push is reviewed** | Charges per push and comments on work in flight. `@claude` covers the real need on demand. |
