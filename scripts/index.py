"""Embed all kb/*.md files into a local ChromaDB collection.

Reads every Markdown file under kb/projects and kb/libraries, splits each into
section-sized chunks, and stores them in a persistent ChromaDB collection using
ChromaDB's built-in local embedding model (all-MiniLM-L6-v2 — no API key, runs
on your machine).

Also fetches live notes from the notes-api service (if running) so notes that
have been classified — with their category:/domain: tags — are searchable via
search_kb(kind="notes"), closing the loop: notes-api → classifier → tags →
knowledge base → kb-agent.

By default the index updates *incrementally*: only chunks whose text is new or
changed are re-embedded, and chunks from deleted/renamed files (or removed notes)
are dropped — so the collection ends up identical to a full rebuild, without
re-embedding everything. Pass --rebuild to drop and re-embed from scratch.

External notes directories come from ``notes_dirs`` in projects.yaml, which holds
absolute workstation paths. ``KB_AGENT_NOTES_DIRS`` overrides that list so any
other environment — CI above all — can reconstruct the same corpus from its own
checkout (kb-agent/ADR-012). A configured directory that does not exist is a hard
error, never a skip: the index would otherwise come out quietly incomplete.

Usage:
    uv run python scripts/index.py             # incremental update
    uv run python scripts/index.py --rebuild   # drop and re-embed everything
    KB_AGENT_NOTES_DIRS=/path/to/learning-notes uv run python scripts/index.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import chromadb
import httpx
import yaml
from rich.console import Console

REPO_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = REPO_ROOT / "kb"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "knowledge_base"
NOTES_API_PROJECT = "notes-api"

# Overrides projects.yaml's `notes_dirs` with an os.pathsep-separated list. This is
# what makes the eval corpus reconstructible outside the author's workstation — CI
# clones learning-notes and points here (kb-agent/ADR-012, system/SYS-017 §3). Set
# it to the empty string to index no external notes at all; unset falls back to
# projects.yaml.
NOTES_DIRS_ENV = "KB_AGENT_NOTES_DIRS"

# Roughly target this many characters per chunk before starting a new one.
MAX_CHUNK_CHARS = 1200

# Repo scaffolding a notes_dir may contain that must never be indexed as notes:
# repo front doors and agent-steering files by name, generated output by
# directory. notes_dirs point at whole repos, so rglob sweeps these up, and
# their chunks crowd real content out of the top-k — GRAPH_REPORT.md chunks sat
# in the top 5 of two of the three unfiltered gold-set misses (2026-08-02
# baseline). Matched case-insensitively; kb/ stubs are generated content and
# never pass through this filter.
SCAFFOLDING_FILENAMES = {"readme.md", "claude.md", "agents.md", "contributing.md"}
SCAFFOLDING_DIRNAMES = {"graphify-out"}

console = Console()


def chunk_markdown(text: str, doc_title: str | None = None) -> list[str]:
    """Split Markdown into chunks, breaking on headings and capping size.

    Each heading starts a new chunk, and any section that grows past
    MAX_CHUNK_CHARS is flushed into its own chunk so no single chunk greatly
    exceeds the budget. Prepends document title and parent section header paths
    (e.g. ``[Document: kb-agent.md > Tech Stack]``) to chunk text before embedding.

    Args:
        text: The full Markdown text of one KB file.
        doc_title: Optional document title or filename (e.g. "kb-agent.md").

    Returns:
        The non-empty, stripped chunks in document order.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    heading_stack: list[tuple[int, str]] = []
    chunk_headers: list[tuple[int, str]] = []

    def format_prefix(headers: list[tuple[int, str]]) -> str:
        effective_title = doc_title
        sec_headers: list[str] = []
        doc_stem = Path(effective_title).stem.lower() if effective_title else None

        for lvl, title in headers:
            title_clean = title.strip()
            if effective_title is None:
                effective_title = title_clean
                doc_stem = Path(effective_title).stem.lower()
            else:
                if lvl == 1 and (
                    title_clean.lower() == effective_title.lower()
                    or (doc_stem and title_clean.lower() == doc_stem)
                ):
                    continue
                sec_headers.append(title_clean)

        if not effective_title:
            if sec_headers:
                path_str = " > ".join(sec_headers)
                return f"[Document: {path_str}]"
            return ""

        if sec_headers:
            path_str = f"{effective_title} > {' > '.join(sec_headers)}"
        else:
            path_str = effective_title

        return f"[Document: {path_str}]"

    def flush() -> None:
        nonlocal current, current_len, chunk_headers
        if current:
            joined = "\n".join(current).strip()
            if joined:
                prefix = format_prefix(chunk_headers)
                if prefix:
                    joined = f"{prefix}\n{joined}"
                chunks.append(joined)
            current = []
            current_len = 0

    for line in text.splitlines():
        strip_line = line.strip()
        is_heading = strip_line.startswith("#")

        if is_heading:
            hash_count = len(strip_line) - len(strip_line.lstrip("#"))
            heading_text = strip_line.lstrip("#").strip()

            if current:
                flush()

            while heading_stack and heading_stack[-1][0] >= hash_count:
                heading_stack.pop()
            heading_stack.append((hash_count, heading_text))
            chunk_headers = list(heading_stack)
        elif current_len >= MAX_CHUNK_CHARS:
            flush()

        if not current:
            chunk_headers = list(heading_stack)

        current.append(line)
        current_len += len(line) + 1

    flush()
    return chunks


def is_note_scaffolding(md_file: Path, notes_dir: Path) -> bool:
    """True when a notes_dir Markdown file is repo scaffolding, not a note.

    A file is scaffolding when its name is in SCAFFOLDING_FILENAMES or any
    directory between notes_dir and the file is in SCAFFOLDING_DIRNAMES.

    Args:
        md_file: The Markdown file found under the notes directory.
        notes_dir: The notes_dir root it was found under.

    Returns:
        True if the file should be excluded from the notes index.
    """
    relative = md_file.relative_to(notes_dir)
    if any(
        part.startswith(".") or part.lower() in SCAFFOLDING_DIRNAMES for part in relative.parts[:-1]
    ):
        return True
    return relative.name.lower() in SCAFFOLDING_FILENAMES


def notes_dirs() -> tuple[list[Path], str]:
    """External directories of hand-written notes to index alongside kb/.

    Configured under ``notes_dirs`` in projects.yaml, whose values are absolute
    paths on the author's workstation. ``KB_AGENT_NOTES_DIRS`` overrides that
    list with an ``os.pathsep``-separated one, which is how any environment that
    is not that workstation — CI above all — reconstructs the corpus from a
    checkout of its own (kb-agent/ADR-012). Setting it to the empty string is a
    real answer meaning "index no external notes"; leaving it unset falls back
    to projects.yaml.

    Returns:
        A ``(dirs, origin)`` tuple. ``origin`` names where the list came from so
        a missing directory can tell the caller which knob to turn.
    """
    override = os.environ.get(NOTES_DIRS_ENV)
    if override is not None:
        dirs = [Path(p) for p in override.split(os.pathsep) if p.strip()]
        return dirs, f"the {NOTES_DIRS_ENV} environment variable"

    projects_file = REPO_ROOT / "projects.yaml"
    if not projects_file.exists():
        return [], "projects.yaml (absent)"
    config = yaml.safe_load(projects_file.read_text(encoding="utf-8")) or {}
    return [Path(p) for p in config.get("notes_dirs", [])], "projects.yaml `notes_dirs`"


def _notes_api_endpoint() -> str | None:
    """Return the notes-api base URL from projects.yaml, or None if not configured."""
    projects_file = REPO_ROOT / "projects.yaml"
    if not projects_file.exists():
        return None
    config = yaml.safe_load(projects_file.read_text(encoding="utf-8")) or {}
    for project in config.get("projects", []):
        if project.get("name") == NOTES_API_PROJECT:
            return project.get("endpoint")
    return None


def _note_to_markdown(note: dict) -> str:
    """Format a note dict as a Markdown string for chunking and indexing."""
    tags = note.get("tags", [])
    tag_line = f"\nTags: {', '.join(tags)}" if tags else ""
    return f"# {note.get('title', 'Untitled')}\n\n{note.get('content', '')}{tag_line}"


def collect_notes_from_api() -> tuple[list[str], list[dict], list[str]]:
    """Fetch live notes from the notes-api and return indexable chunks.

    Calls GET /notes on the configured notes-api endpoint and converts each
    note (including its classifier-written tags) into one or more chunks with
    kind="notes" metadata. This is the "Tags → Knowledge base" leg of the
    portfolio system diagram.

    Gracefully returns empty arrays — with a yellow warning — if the service
    is not configured or is unreachable, so index.py continues without notes
    rather than failing entirely.

    Returns:
        A ``(documents, metadatas, ids)`` tuple of equal-length lists, ready
        to merge into collect_documents().
    """
    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    endpoint = _notes_api_endpoint()
    if not endpoint:
        return documents, metadatas, ids

    url = endpoint.rstrip("/") + "/notes"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        notes = resp.json()
    except Exception as exc:
        console.print(
            f"[yellow]notes-api unreachable at {url}, skipping live notes: {exc}[/yellow]"
        )
        return documents, metadatas, ids

    if not isinstance(notes, list):
        console.print("[yellow]notes-api returned unexpected shape, skipping.[/yellow]")
        return documents, metadatas, ids

    for note in notes:
        if not isinstance(note, dict):
            continue
        note_id = note.get("id", "unknown")
        note_title = str(note.get("title", "Untitled"))
        text = _note_to_markdown(note)
        for i, chunk in enumerate(chunk_markdown(text, doc_title=note_title)):
            documents.append(chunk)
            metadatas.append(
                {"source": f"notes-api/note/{note_id}", "kind": "notes", "name": str(note_id)}
            )
            ids.append(f"notes/api/{note_id}#{i}")

    if documents:
        console.print(
            f"[green]Fetched {len(notes)} note(s) from notes-api "
            f"→ {len(documents)} chunk(s).[/green]"
        )

    return documents, metadatas, ids


def _add_file(
    md_file: Path,
    kind: str,
    source: str,
    documents: list[str],
    metadatas: list[dict],
    ids: list[str],
) -> None:
    """Chunk one Markdown file and append its chunks to the parallel arrays."""
    text = md_file.read_text(encoding="utf-8")
    for i, chunk in enumerate(chunk_markdown(text, doc_title=md_file.name)):
        documents.append(chunk)
        metadatas.append({"source": source, "kind": kind, "name": md_file.stem})
        ids.append(f"{kind}/{md_file.stem}#{i}")


def collect_documents() -> tuple[list[str], list[dict], list[str]]:
    """Build the parallel arrays ChromaDB's add() expects, one entry per chunk.

    Walks the in-repo kb/ tree (kind = parent folder, e.g. ``projects`` /
    ``libraries``) plus every external notes directory from ``notes_dirs()``
    (kind = ``notes``) and live notes from the notes-api service
    (kind = ``notes``). Repo scaffolding inside a notes_dir (README/CLAUDE.md,
    generated output — see is_note_scaffolding) is excluded from the notes
    sweep. Each metadata dict carries ``source``, ``kind``, and ``name``.

    A note's ``source`` is ``<notes_dir name>/<filename>``, so the *directory
    name* is part of the wire format the gold set matches against — a corpus
    cloned as ``learning-notes/`` and one cloned as ``notes/`` are not
    interchangeable.

    Returns:
        A ``(documents, metadatas, ids)`` tuple of equal-length lists.

    Raises:
        FileNotFoundError: if a configured notes directory does not exist.
    """
    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for md_file in sorted(KB_DIR.rglob("*.md")):
        _add_file(
            md_file,
            md_file.parent.name,  # "projects" or "libraries"
            str(md_file.relative_to(REPO_ROOT)),
            documents,
            metadatas,
            ids,
        )

    configured_notes_dirs, origin = notes_dirs()
    for notes_dir in configured_notes_dirs:
        # Hard error, not a skip. A configured-but-absent corpus produces an index
        # that is quietly missing 12 of the gold set's 27 queries' sources, so the
        # eval scores them as retrieval misses while every step stays green — the
        # exact "a gate that cannot fail is theater" failure system/SYS-017 names,
        # and the reason corpus provenance is that decision's tier-1 entry
        # condition. If the notes really should not be indexed here, say so
        # explicitly rather than by omission.
        if not notes_dir.exists():
            # ASCII only: this surfaces on a Windows console (cp1252) as well as in
            # CI, and the same rule is why scripts/lint_decisions.py avoids Unicode.
            raise FileNotFoundError(
                f"notes_dir does not exist: {notes_dir}\n"
                f"It came from {origin}. The index would silently omit this corpus, "
                f"and any eval run against it would report missing documents as bad "
                f"retrieval (kb-agent/ADR-012, system/SYS-017 section 3).\n"
                f"Fix by checking the corpus out at that path, or point "
                f"{NOTES_DIRS_ENV} at where it actually lives. To index no external "
                f'notes at all, set {NOTES_DIRS_ENV}="" - an empty value is an '
                f"answer; an absent directory is not."
            )
        skipped = 0
        for md_file in sorted(notes_dir.rglob("*.md")):
            if is_note_scaffolding(md_file, notes_dir):
                skipped += 1
                continue
            _add_file(
                md_file,
                "notes",
                f"{notes_dir.name}/{md_file.name}",
                documents,
                metadatas,
                ids,
            )
        if skipped:
            console.print(
                f"[dim]{notes_dir.name}: skipped {skipped} scaffolding file(s) "
                f"(README/CLAUDE.md/generated output)[/dim]"
            )

    # Pull live notes from the notes-api (closes the "tags → knowledge base" loop).
    api_docs, api_metas, api_ids = collect_notes_from_api()
    documents.extend(api_docs)
    metadatas.extend(api_metas)
    ids.extend(api_ids)

    return documents, metadatas, ids


def plan_index_update(
    desired_ids: list[str],
    desired_docs: list[str],
    existing_docs: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Diff the freshly-collected chunk set against what's already embedded.

    The persisted collection *is* the record of what was indexed last run, so no
    separate manifest is needed: compare each desired chunk's text against the
    stored text for the same id. Metadata (``source``/``kind``/``name``) is
    derived from the id's path, so it can't change without the id changing —
    comparing the embedding-relevant text alone is sufficient.

    Args:
        desired_ids: Chunk ids for the target state (from collect_documents).
        desired_docs: Chunk texts, parallel to desired_ids.
        existing_docs: id -> stored text for chunks currently in the collection.

    Returns:
        An ``(upsert_ids, delete_ids)`` tuple: ids whose text is new or changed
        (need re-embedding), and ids no longer desired (stale — from deleted or
        renamed files, or notes that disappeared).
    """
    desired = dict(zip(desired_ids, desired_docs))
    upsert = [cid for cid, doc in desired.items() if existing_docs.get(cid) != doc]
    delete = [cid for cid in existing_docs if cid not in desired]
    return upsert, delete


def main(argv: list[str] | None = None) -> None:
    """Update the ChromaDB collection from every chunk under kb/.

    Incrementally by default — re-embeds only new/changed chunks and drops stale
    ones, leaving the collection identical to a full rebuild. Pass --rebuild to
    drop and re-embed from scratch. Prints a one-line summary either way.

    Args:
        argv: CLI args to parse; defaults to ``sys.argv`` when None. Passing an
            explicit list (e.g. ``[]`` or ``["--rebuild"]``) lets callers and
            tests drive it without touching the process argv.
    """
    parser = argparse.ArgumentParser(description="Index KB Markdown into ChromaDB.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop the collection and re-embed everything from scratch.",
    )
    args = parser.parse_args(argv)

    documents, metadatas, ids = collect_documents()

    if not documents:
        console.print("[yellow]No Markdown files found under kb/. Run ingest.py first.[/yellow]")
        return

    # Embedded PersistentClient only — never HttpClient/server mode — and the
    # collection is written *only* by this pipeline from locally-generated kb/
    # content (never an external/untrusted source), living in a local, gitignored,
    # unshared chroma_db/. Those are load-bearing for CVE-2026-45829's risk
    # assessment (see docs/notes/chromadb-cve-2026-45829-assessment.md) — note the
    # assessment holds under both --rebuild and the incremental path, since neither
    # introduces a new writer or a custom embedding_function; re-read it before
    # changing how this client is opened or how the collection is populated.
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Each metadata dict IS a valid ChromaDB Metadata (Mapping[str, ...]), but
    # `list` is invariant: list[dict] isn't assignable to List[Metadata], so the
    # type checker flags add()/upsert(). Correct at runtime; ignore just that arg.
    if args.rebuild:
        # Escape hatch: nuke-and-pave, the original behavior.
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # Collection didn't exist yet — fine.
        collection = client.create_collection(COLLECTION_NAME)
        collection.add(documents=documents, metadatas=metadatas, ids=ids)  # type: ignore[arg-type]
        console.print(
            f"[bold green]Rebuilt[/bold green] {len(documents)} chunks "
            f"from {len({m['source'] for m in metadatas})} files "
            f"into '{COLLECTION_NAME}' at {CHROMA_DIR.name}/"
        )
        return

    # Incremental: upsert only new/changed chunks, delete stale ones.
    collection = client.get_or_create_collection(COLLECTION_NAME)
    existing = collection.get(include=["documents"])
    existing_docs = dict(zip(existing["ids"], existing["documents"] or []))
    upsert_ids, delete_ids = plan_index_update(ids, documents, existing_docs)

    index_of = {cid: i for i, cid in enumerate(ids)}
    if delete_ids:
        collection.delete(ids=delete_ids)
    if upsert_ids:
        collection.upsert(
            ids=upsert_ids,
            documents=[documents[index_of[cid]] for cid in upsert_ids],
            metadatas=[metadatas[index_of[cid]] for cid in upsert_ids],  # type: ignore[arg-type]
        )

    console.print(
        f"[bold green]Indexed[/bold green] {len(ids)} chunks "
        f"from {len({m['source'] for m in metadatas})} files into '{COLLECTION_NAME}' "
        f"([green]{len(upsert_ids)}[/green] embedded/updated, "
        f"[yellow]{len(delete_ids)}[/yellow] removed) at {CHROMA_DIR.name}/"
    )


if __name__ == "__main__":
    main()
