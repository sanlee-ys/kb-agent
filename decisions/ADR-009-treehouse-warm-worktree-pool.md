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

## Downstream surfaces

| Surface | State |
|---|---|
| `treehouse.toml` | **New.** The scoping mechanism itself — its presence in this repo and nowhere else *is* the decision. `max_trees = 4` is deliberate; the comment in the file says why so it survives a casual edit. |
| `decisions/README.md` | **Row added.** |
| `CLAUDE.md` (this repo) | **Line added** under Commands. A session working here needs to know a pool exists, that trees arrive detached, and that `return` hard-kills on Windows. |
| **Global `CLAUDE.md`** | **Deliberately unchanged**, and this is the surface a reader would most expect to move. `claude --worktree` remains the house default for every other repo. If that ever changes, this ADR is what has to be revisited first. |
| `~/go/bin/treehouse.exe` | **Un-versioned machine state**, present only on the Windows PC and pinned to commit `c0b7f68`. It is not provisioned by `dotfiles`, so a second machine gets no pool until the same `go install` is run there. The durable fix is versioning the install, not documenting it here. |
| `~/.config/treehouse/config.toml` | **Deliberately not created.** It is the only file that can carry a `post_create` hook — `config.Load` zeroes hooks read from repo-level `treehouse.toml` — so it is the one place the cold-first-lease cost could be fixed. Rejected on the measurements in Consequences. If it is ever added, it belongs in `dotfiles` next to the pinned `go install`, not hand-written on one machine. |
| `.gitignore` | **Unchanged, deliberately.** Worktrees live under `$HOME/.treehouse/`, not in the repo, so the pool leaves nothing to ignore. |
| `chroma_db/` | **Unchanged and still git-ignored.** It is the *reason* for this ADR, not a thing this ADR alters — the pool preserves it between sessions rather than committing it. |
| [ADR-011](ADR-011-incremental-index-by-default.md) | Its incremental-index behaviour is what makes a warm pool worth having: a preserved `chroma_db/` plus incremental indexing means a reused worktree re-embeds only what changed. |
| `career/ideas.md` | Updated the same day with the audit, the install method, and the `treehouse update` warning. Private repo; the operative record is this ADR. |

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
- **The pool warms on *reuse*, never on *creation* — so the first lease of each
  tree is cold, and that is expected, not a cache failure.** `treehouse get`
  reuses an idle tree by running `checkout --detach --force` → `reset --hard` →
  `clean -fd`. That `clean` has **no `-x`**, so git leaves ignored files alone
  and `.venv/` and `chroma_db/` survive — *that* is the entire warm-cache
  mechanism. When no idle tree exists and the pool is under `max_trees`, it
  instead runs `git worktree add`, which produces a bare checkout carrying no
  ignored files at all; nothing copies a sibling tree's venv or index in. So a
  pool of `max_trees = 4` pays the ~458 MB venv and a full re-embed **up to four
  times**, once per tree, before it is fully warm. Two sessions starting minutes
  apart against an empty pool both get cold trees. The only hook that could
  pre-warm a new tree is `post_create`, and treehouse deliberately **ignores
  hooks in repo-level `treehouse.toml`** — they are honoured only from the
  user-level `~/.config/treehouse/config.toml`, so this cannot be fixed by a
  committed file.

  **A `post_create` hook was investigated on 2026-08-01 and rejected.** It would
  work — `acquire` sets `runPostCreate` on both the create and the reuse path,
  and `hooks.Run` only logs failures — but it buys back far less than this ADR
  assumed. Measured here: a cold `uv sync` is **7.5 s**, because uv hardlinks the
  458 MB out of its global cache instead of re-downloading it (the disk figure
  was never a time figure), and a cold re-embed is **24.8 s** — about **32 s**,
  up to four times, once per pool, ever. Against that, the hook runs
  synchronously inside `acquire` on *every* acquisition, and on an already-warm
  tree a satisfied `uv sync` plus a no-op incremental index still costs
  **6.3 s**, nearly all of it loading the embedding model just to open the
  collection. It breaks even somewhere around twenty acquisitions and is
  net-negative forever after, because the pool warms permanently but the tax
  never stops. Three things then sealed it: **there is no per-repo hook
  scoping** — `config.Hooks` is a flat `{post_create, pre_destroy}` and
  `loadUser` reads a single file, so the hook would run this repo's build
  commands in every repo treehouse ever touches, scopable only by hand-rolled
  `cmd.exe` conditionals (the Windows hook shell is `%COMSPEC% /d /s /c`);
  **the cold state already announces itself**, because `search_kb` on an
  unindexed tree returns a SYS-003 *error* whose `next_actions` reads "Run
  scripts/index.py to build the index, then retry this search", making a cold
  tree a legible 25-second fix rather than a silent trap; and it would add **a
  second piece of un-versioned machine state** beside `~/go/bin/treehouse.exe`,
  and a worse one — a missing binary fails loudly, a missing hook is
  indistinguishable from a normal cold tree. Versioning it in `dotfiles` would
  close that gap, at the cost of putting one repo's build commands into
  machine-global config. Cleared rather than held against it: the hook **cannot**
  race `treehouse return`, since it completes before the lease is handed over,
  and an interrupted index self-heals — `plan_index_update` diffs desired chunks
  against what is actually in the collection and re-embeds only what is missing.
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
