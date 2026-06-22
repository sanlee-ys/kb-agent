"""Scan a project directory and auto-generate Markdown KB stubs.

For each project listed in projects.yaml, this reads its dependency manifest
(pyproject.toml or requirements.txt) and README, then uses the Anthropic API to
write:

  - kb/projects/<name>.md   — a one-page overview of the project
  - kb/libraries/<pkg>.md   — a short explainer per dependency

Stubs are NEVER overwritten once they exist, so hand-annotations you add later
are preserved. Use --force to regenerate.

Usage:
    uv run python scripts/ingest.py                 # all projects in projects.yaml
    uv run python scripts/ingest.py defense-news-classifier   # just one
    uv run python scripts/ingest.py --force         # regenerate existing stubs
"""

from __future__ import annotations

import argparse
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

# SYS-002 model-tier standard: the Sonnet workhorse is the default for stub
# generation; bump this only if an eval shows a stronger tier writes better stubs.
MODEL = "claude-sonnet-4-6"

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
{readme[:8000] or "(no README found)"}
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


def main() -> None:
    """Generate KB stubs for the projects named on the command line (or all).

    Parses CLI args, then for each project writes a project overview plus one
    library stub per dependency, skipping existing files unless --force is set.
    """
    parser = argparse.ArgumentParser(description="Generate KB stubs from projects.")
    parser.add_argument("project", nargs="?", help="Only ingest this project by name.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing stubs.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    client = anthropic.Anthropic()

    KB_PROJECTS.mkdir(parents=True, exist_ok=True)
    KB_LIBRARIES.mkdir(parents=True, exist_ok=True)

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

        # Project stub.
        project_file = KB_PROJECTS / f"{name}.md"
        if project_file.exists() and not args.force:
            console.print(f"  project stub exists, skipping ({project_file.name})")
        else:
            stub = generate_project_stub(client, name, description, deps, readme)
            write_stub(project_file, stub, args.force)
            console.print(f"  [green]wrote[/green] {project_file.name}")

        # Library stubs (one per dependency).
        for pkg in deps:
            lib_file = KB_LIBRARIES / f"{pkg}.md"
            if lib_file.exists() and not args.force:
                continue
            stub = generate_library_stub(client, pkg)
            write_stub(lib_file, stub, args.force)
            console.print(f"  [green]wrote[/green] libraries/{lib_file.name}")

    console.print("[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
