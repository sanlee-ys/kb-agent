# ADR-009: A warm worktree pool for this repo, and only this repo

**Status:** Accepted
**Date:** 2026-07-26
**Deciders:** San Lee

---

## Context

The house rule for running several Claude sessions against one repo is
`claude --worktree <name>` (global `CLAUDE.md`). It gives each session an
isolated checkout, which is the property that matters, but it gives a **cold**
one every time: no `.venv`, no generated index, nothing warm.

[`treehouse`](https://github.com/kunchenguid/treehouse) (Go, MIT) maintains a
pool of reusable worktrees instead, preserving dependencies and build cache
between sessions. It was evaluated as a possible replacement for
`claude --worktree` everywhere. **It is not one.**

**The cost of a cold worktree is repo-specific, and almost every repo here is
cheap.** Measured the same day: `notes-api` creates its virtualenv and installs
44 packages in **34 ms** — on a Raspberry Pi. `defense-news-classifier`,
`career` and `portfolio` are the same shape. A warm pool saves them nothing.

**This repo is the exception**, for two reasons already recorded in
`CLAUDE.md`:

- The `.venv` is **~458 MB** (sentence-transformers and its stack), against
  `notes-api`'s handful of megabytes.
- `chroma_db/` is generated and git-ignored, so a fresh worktree has **no index
  at all** and must re-embed the entire KB via `scripts/index.py` before
  `search_kb` answers anything. The persisted index is only ~4 MB on disk; the
  cost is the embedding pass, not the bytes.

## Decision

**Adopt `treehouse` for this repo only, via the `treehouse.toml` committed
alongside this ADR. Do not adopt it globally, and do not amend the global
`CLAUDE.md`.** `claude --worktree` remains the default everywhere else.

**`max_trees = 4`, not the tool's default of 16.** Sixteen worktrees at ~458 MB
of virtualenv each reserves ~7.3 GB for a single repo. Four covers realistic
parallel-session load here and bounds the pool at ~1.8 GB.

**Install from source, pinned to an audited commit.** The Windows install path
upstream offers is `irm … | iex`. This is a tool that runs `git reset --hard`
and terminates processes; it was instead built with
`go install github.com/kunchenguid/treehouse@c0b7f685d4511eec765ab4cbb47583178424eb45`,
which is simultaneously tag `v2.1.0` and `main`, and is the exact tree that was
read before adoption.

## Consequences

**What this costs, accepted knowingly:**

- **A warm cache is deliberate stale state.** A cold worktree proves the repo
  builds from scratch; a warm one can mask a stale lockfile, a removed
  dependency still resident in `.venv`, or an index that no longer matches
  `kb/`. **When a result here is surprising, re-run in a cold checkout before
  believing it.** This is the same class of problem as trusting a green that
  never ran.
- **Process termination is ungraceful on Windows.** `treehouse return` kills
  every process whose working directory is inside the worktree. On Unix that is
  `SIGTERM` → grace period → `SIGKILL`; on Windows the source uses
  `TerminateProcess` with no grace period, so anything mid-write dies unflushed.
  Do not `return` a worktree with an indexing run in flight.
- **Worktrees are handed out with a detached `HEAD`.** That cuts against the
  one-concern-one-branch-one-PR rule, and the failure mode is quiet: committed
  work left on a detached `HEAD` in an idle pooled worktree reads as *clean*, so
  the next acquisition resets over it. Reflog-recoverable, not obvious. **Branch
  and push before leaving a worktree idle.**
- **Two worktree systems now coexist.** Background-task sessions still use the
  built-in mechanism, and neither system knows about the other's trees.
- **`treehouse update` must not be run on this install.** Upstream ships `v2.x`
  tags while `go.mod` declares `module github.com/kunchenguid/treehouse` with no
  `/v2` suffix, so `go install …@v2.1.0` fails outright and the source build
  reports a pseudo-version of `v1.8.1-…`. The updater would read that as far
  behind and replace the audited build with a downloaded binary. Update by
  re-running `go install` at a newly audited commit.

**What made this safe enough to adopt at all:** the `reset --hard` /
`clean -fd` path is guarded four ways before it fires — the pool skips
worktrees that are `Destroying`, `Leased`, owned by a live process, in use by
an OS-level process check, **or dirty** — all under a state lock, and it errors
with a usable message when everything is busy rather than forcing. Uncommitted
work causes a worktree to be **skipped, not wiped**.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Adopt `treehouse` globally, replacing `claude --worktree` | Pays the costs above across every repo to buy a warm cache that only this one needs. `notes-api` provisions in 34 ms; there is nothing to warm |
| Keep `claude --worktree` here too | Every fresh session re-creates a ~458 MB venv and re-embeds the whole KB before the agent can answer anything. That is the one place the cold start is a real tax |
| Commit `chroma_db/` so fresh worktrees start indexed | Reverses a standing convention for a generated artifact, and would put a binary index under review in every diff |
| Install via the upstream `irm … \| iex` one-liner | Pipes a remote script into the shell to obtain a binary that resets repos and kills processes. Building the audited commit costs one command more |
| Leave `max_trees` at the default 16 | ~7.3 GB of virtualenv reserved for one repo, for parallelism that is never used |
