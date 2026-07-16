"""Scan a project directory and auto-generate Markdown KB stubs.

For each project listed in projects.yaml, this reads its dependency manifest
(pyproject.toml or requirements.txt) and README, then uses the Anthropic API to
write:

  - kb/projects/<name>.md   — a one-page overview of the project
  - kb/libraries/<pkg>.md   — a short explainer per dependency

Stubs are NEVER overwritten once they exist, so hand-annotations you add later
are preserved. Use --force to regenerate.

A sidecar manifest (kb/.ingest-manifest.json) fingerprints the source each
project stub was generated from, so --check can flag stubs that have drifted
from their source without touching or regenerating anything.

Usage:
    uv run python scripts/ingest.py                 # all projects in projects.yaml
    uv run python scripts/ingest.py defense-news-classifier   # just one
    uv run python scripts/ingest.py --force         # regenerate existing stubs
    uv run python scripts/ingest.py --check         # report stubs that drifted (no changes)
    uv run python scripts/ingest.py --accept        # bless current source as the baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv
from rich.console import Console

# Resolve paths relative to the repo root (this file lives in scripts/).
REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "projects.yaml"
KB_PROJECTS = REPO_ROOT / "kb" / "projects"
KB_LIBRARIES = REPO_ROOT / "kb" / "libraries"

# Provenance sidecar: records the source fingerprint each project stub was
# generated from, so --check can tell when a stub has drifted from its source.
# Lives beside the stubs but is JSON (not *.md), so index.py never embeds it.
MANIFEST_FILE = REPO_ROOT / "kb" / ".ingest-manifest.json"
MANIFEST_VERSION = 1

# How much of a README is put in the generation prompt. The freshness
# fingerprint hashes exactly this prefix, so "stale" means "the inputs that
# produced this stub changed" — not "a byte past what the model ever saw."
README_PROMPT_CHARS = 8000

# SYS-002 model-tier standard: the Sonnet workhorse is the default for stub
# generation; bump this only if an eval shows a stronger tier writes better stubs.
MODEL = "claude-sonnet-5"

console = Console()


def parse_dependencies(project_path: Path) -> list[str]:
    """Return a sorted list of bare package names from a project's manifest.

    Prefers pyproject.toml ([project].dependencies); falls back to
    requirements.txt. Version specifiers, extras, and markers are stripped so
    "anthropic[mcp]>=0.40; python_version>='3.11'" becomes "anthropic".

    Args:
        project_path: Path to the project's root directory.

    Returns:
        Sorted, lower-cased package names, or an empty list if no manifest is
        found.
    """
    raw: list[str] = []

    pyproject = project_path / "pyproject.toml"
    requirements = project_path / "requirements.txt"

    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        raw = data.get("project", {}).get("dependencies", [])
    elif requirements.exists():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                raw.append(line)

    names = set()
    for spec in raw:
        # Take everything before the first version/extra/marker character.
        name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower())
    return sorted(names)


def read_readme(project_path: Path) -> str:
    """Return the project's README text, or empty string if none found.

    Args:
        project_path: Path to the project's root directory.

    Returns:
        The first matching README's text (.md/.rst/.txt/no-extension, in that
        order), or an empty string if none exists.
    """
    for candidate in ("README.md", "README.rst", "README.txt", "README"):
        readme = project_path / candidate
        if readme.exists():
            return readme.read_text(encoding="utf-8", errors="replace")
    return ""


def generate_project_stub(
    client: anthropic.Anthropic,
    name: str,
    description: str,
    deps: list[str],
    readme: str,
) -> str:
    """Ask the model to write a one-page project overview in Markdown.

    Args:
        client: An initialized Anthropic client.
        name: The project name.
        description: The project's stated description from projects.yaml.
        deps: Detected dependency package names.
        readme: The project's README text (truncated when put in the prompt).

    Returns:
        The generated Markdown stub text.
    """
    prompt = f"""Write a concise knowledge-base entry (Markdown) for a software project.

Project name: {name}
Stated description: {description}
Dependencies: {", ".join(deps) or "none detected"}

README (may be empty or partial):
---
{readme[:README_PROMPT_CHARS] or "(no README found)"}
---

Write the entry with these sections:
# {name}
- A 2-3 sentence overview of what the project does.
## Tech stack
- Bullet list of the key libraries and what each is used for in THIS project.
## Notes
- Leave a short placeholder line inviting the author to add design decisions.

Keep it factual. Do not invent features not implied by the inputs."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(b.text for b in response.content if b.type == "text")


def generate_library_stub(client: anthropic.Anthropic, pkg: str) -> str:
    """Ask the model to write a short explainer for a single library.

    Args:
        client: An initialized Anthropic client.
        pkg: The library/package name to explain.

    Returns:
        The generated Markdown stub text.
    """
    prompt = f"""Write a short knowledge-base entry (Markdown) for the Python library "{pkg}".

Structure:
# {pkg}
- One sentence: what it is.
## What it's for
- 2-4 bullets on common use cases.
## Gotchas
- 1-2 bullets on common pitfalls, if any are well known.

Be accurate. If you are unsure what "{pkg}" is, say so plainly rather than guessing."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(b.text for b in response.content if b.type == "text")


def write_stub(path: Path, content: str, force: bool) -> bool:
    """Write content to path unless a file is already there.

    Args:
        path: Destination file path.
        content: Markdown text to write (trailing whitespace is normalized).
        force: When True, overwrite an existing file instead of skipping it.

    Returns:
        True if the file was written, False if it existed and was skipped.
    """
    if path.exists() and not force:
        return False
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return True


def source_fingerprint(description: str, deps: list[str], readme: str) -> str:
    """Return a stable digest of the inputs a project stub is generated from.

    The fingerprint covers exactly what feeds generate_project_stub — the
    projects.yaml description, the sorted dependency names, and the README
    prefix actually put in the prompt — so a change to any of them marks the
    stub stale, while hand-edits to the stub itself (which change none of these
    inputs) never do. README newlines are normalized so a pure CRLF/LF flip
    isn't mistaken for a content change.

    Args:
        description: The project's description from projects.yaml.
        deps: Sorted dependency package names, as parse_dependencies returns.
        readme: The project's full README text; only the prompt-visible prefix
            is fingerprinted.

    Returns:
        A ``"sha256:<hex>"`` digest string.
    """
    payload = json.dumps(
        {
            "description": description,
            "deps": deps,
            "readme": readme[:README_PROMPT_CHARS].replace("\r\n", "\n"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_manifest() -> dict:
    """Load the ingest fingerprint manifest, or an empty skeleton if absent.

    Returns:
        A dict shaped ``{"version": int, "projects": {name: {"fingerprint": ...}}}``.
        A missing file yields an empty skeleton rather than an error, so a fresh
        checkout with no manifest yet is a normal, handled state.
    """
    if not MANIFEST_FILE.exists():
        return {"version": MANIFEST_VERSION, "projects": {}}
    data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    data.setdefault("version", MANIFEST_VERSION)
    data.setdefault("projects", {})
    return data


def save_manifest(manifest: dict) -> None:
    """Write the manifest to disk as pretty, stably-ordered JSON.

    Args:
        manifest: The manifest dict to persist.
    """
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def record_fingerprint(manifest: dict, name: str, fingerprint: str) -> None:
    """Set the recorded source fingerprint for one project in the manifest.

    Args:
        manifest: The manifest dict to mutate in place.
        name: The project name.
        fingerprint: The source fingerprint to record as the new baseline.
    """
    manifest.setdefault("projects", {})[name] = {"fingerprint": fingerprint}


def check_project_freshness(entry: dict, manifest: dict) -> tuple[str, str]:
    """Classify one project stub's freshness against its recorded baseline.

    Args:
        entry: A projects.yaml entry (needs ``name`` and ``path``).
        manifest: The loaded fingerprint manifest.

    Returns:
        A ``(status, detail)`` tuple. ``status`` is one of:

        - ``"skipped"``  — the source path doesn't exist on this machine, so
          freshness can't be computed here (like ingest's own path skip).
        - ``"missing"``  — the source is present but no stub has been generated.
        - ``"untracked"`` — a stub exists but has no recorded baseline (generated
          before freshness tracking, or hand-maintained).
        - ``"stale"``    — the source changed since the stub's baseline.
        - ``"fresh"``    — the source matches the recorded baseline.
    """
    name = entry["name"]
    project_path = Path(entry["path"])
    stub = KB_PROJECTS / f"{name}.md"
    recorded = manifest.get("projects", {}).get(name, {}).get("fingerprint")

    if not project_path.exists():
        return "skipped", f"source path not found ({project_path})"
    if not stub.exists():
        return "missing", "no stub yet — run ingest.py to generate it"

    current = source_fingerprint(
        entry.get("description", ""),
        parse_dependencies(project_path),
        read_readme(project_path),
    )
    if recorded is None:
        return "untracked", "no baseline — run 'ingest.py --accept' (or --force to regenerate)"
    if recorded != current:
        return "stale", "source changed since generation — review, then --accept or --force"
    return "fresh", "up to date"


def orphan_stub_names(projects: list[dict]) -> list[str]:
    """Return project-stub names on disk with no matching projects.yaml entry.

    These are unmanaged by the pipeline — e.g. the hand-written kb-agent.md
    self-stub — so ``--check`` lists them as informational rather than fresh or
    stale (there's no source to compare against).

    Args:
        projects: The projects.yaml entries.

    Returns:
        Sorted stub names (file stems) with no corresponding project entry.
    """
    if not KB_PROJECTS.exists():
        return []
    known = {p.get("name") for p in projects}
    return sorted(f.stem for f in KB_PROJECTS.glob("*.md") if f.stem not in known)


def load_projects(only: str | None) -> list[dict]:
    """Load project entries from projects.yaml, optionally filtered by name.

    Exits the process with an error if projects.yaml is missing or the
    requested name isn't found.

    Args:
        only: A single project name to keep, or None to load all entries.

    Returns:
        The matching project entry dicts from projects.yaml.
    """
    if not PROJECTS_FILE.exists():
        console.print(f"[red]Missing {PROJECTS_FILE}[/red]")
        sys.exit(1)
    config = yaml.safe_load(PROJECTS_FILE.read_text(encoding="utf-8")) or {}
    projects = config.get("projects", [])
    if only:
        projects = [p for p in projects if p.get("name") == only]
        if not projects:
            console.print(f"[red]No project named '{only}' in projects.yaml[/red]")
            sys.exit(1)
    return projects


# Rich color per freshness status, for the --check report.
_STATUS_COLOR = {
    "fresh": "green",
    "stale": "red",
    "untracked": "yellow",
    "missing": "yellow",
    "skipped": "dim",
}


def run_check(projects: list[dict], show_orphans: bool = True) -> int:
    """Report each project stub's freshness and return a process exit code.

    Freshness tracks *project* stubs only — library stubs are generated from a
    package name, not a source file, so they can't drift against one. Prints a
    line per project plus, on an unfiltered sweep, any unmanaged orphan stubs.

    Args:
        projects: The projects.yaml entries to check.
        show_orphans: Whether to list stubs with no projects.yaml entry. Only
            valid on a full sweep — with a name-filtered ``projects`` the "known"
            set is incomplete, so every other managed stub would look orphaned.

    Returns:
        1 if any stub is stale (actionable drift), else 0 — so this can gate CI
        or a pre-commit hook later.
    """
    manifest = load_manifest()
    stale = 0
    for entry in projects:
        status, detail = check_project_freshness(entry, manifest)
        stale += status == "stale"
        color = _STATUS_COLOR.get(status, "white")
        console.print(f"  [{color}]{status:>9}[/{color}]  {entry['name']} — {detail}")

    if show_orphans:
        for name in orphan_stub_names(projects):
            console.print(f"  [dim]unmanaged[/dim]  {name} — not in projects.yaml (hand-edited)")

    if stale:
        console.print(
            f"[red]{stale} stub(s) stale.[/red] Review, then 'ingest.py --accept' or --force."
        )
    else:
        console.print("[green]No stale stubs.[/green]")
    return 1 if stale else 0


def run_accept(projects: list[dict]) -> None:
    """Record current source fingerprints as the baseline for existing stubs.

    This blesses hand-curated stubs as matching their current source without
    regenerating them — the non-destructive way to establish a baseline, unlike
    --force which overwrites hand-edits. Projects with no stub, or whose source
    path is absent on this machine, are left untouched.

    Args:
        projects: The projects.yaml entries to accept.
    """
    manifest = load_manifest()
    recorded: list[str] = []
    for entry in projects:
        name = entry["name"]
        project_path = Path(entry["path"])
        if not project_path.exists() or not (KB_PROJECTS / f"{name}.md").exists():
            continue
        fingerprint = source_fingerprint(
            entry.get("description", ""),
            parse_dependencies(project_path),
            read_readme(project_path),
        )
        record_fingerprint(manifest, name, fingerprint)
        recorded.append(name)

    if recorded:
        save_manifest(manifest)
        console.print(f"[green]Recorded baseline for:[/green] {', '.join(recorded)}")
    else:
        console.print(
            "[yellow]No stubs to record (no source paths present, or no stubs yet).[/yellow]"
        )


def main() -> None:
    """Generate KB stubs for the projects named on the command line (or all).

    Parses CLI args, then for each project writes a project overview plus one
    library stub per dependency, skipping existing files unless --force is set.
    The read-only --check and non-destructive --accept modes short-circuit
    before any model call, since neither needs the API.
    """
    parser = argparse.ArgumentParser(description="Generate KB stubs from projects.")
    parser.add_argument("project", nargs="?", help="Only ingest this project by name.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing stubs.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report which project stubs have drifted from their source; make no changes.",
    )
    parser.add_argument(
        "--accept",
        action="store_true",
        help="Record current source as each existing stub's baseline (no regeneration).",
    )
    args = parser.parse_args()

    # --check and --accept are offline (no model call), so handle them first.
    # The orphan sweep is global, so only run it when unfiltered — a name-filtered
    # check has an incomplete "known" set and would mislabel every sibling stub.
    if args.check:
        sys.exit(run_check(load_projects(args.project), show_orphans=args.project is None))
    if args.accept:
        run_accept(load_projects(args.project))
        return

    load_dotenv(REPO_ROOT / ".env")
    client = anthropic.Anthropic()

    KB_PROJECTS.mkdir(parents=True, exist_ok=True)
    KB_LIBRARIES.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    manifest_changed = False
    for entry in load_projects(args.project):
        name = entry["name"]
        project_path = Path(entry["path"])
        description = entry.get("description", "")

        if not project_path.exists():
            console.print(f"[yellow]Skipping {name}: path not found ({project_path})[/yellow]")
            continue

        console.print(f"[bold]Ingesting {name}[/bold]")
        deps = parse_dependencies(project_path)
        readme = read_readme(project_path)

        # Project stub. Record the source fingerprint only when we actually
        # (re)write it, so the manifest baseline reflects what produced the stub
        # — never a stale stub we skipped over.
        project_file = KB_PROJECTS / f"{name}.md"
        if project_file.exists() and not args.force:
            console.print(f"  project stub exists, skipping ({project_file.name})")
        else:
            stub = generate_project_stub(client, name, description, deps, readme)
            write_stub(project_file, stub, args.force)
            record_fingerprint(manifest, name, source_fingerprint(description, deps, readme))
            manifest_changed = True
            console.print(f"  [green]wrote[/green] {project_file.name}")

        # Library stubs (one per dependency).
        for pkg in deps:
            lib_file = KB_LIBRARIES / f"{pkg}.md"
            if lib_file.exists() and not args.force:
                continue
            stub = generate_library_stub(client, pkg)
            write_stub(lib_file, stub, args.force)
            console.print(f"  [green]wrote[/green] libraries/{lib_file.name}")

    if manifest_changed:
        save_manifest(manifest)
    console.print("[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
