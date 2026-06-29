"""Embed all kb/*.md files into a local ChromaDB collection.

Reads every Markdown file under kb/projects and kb/libraries, splits each into
section-sized chunks, and stores them in a persistent ChromaDB collection using
ChromaDB's built-in local embedding model (all-MiniLM-L6-v2 — no API key, runs
on your machine).

Also fetches live notes from the notes-api service (if running) so notes that
have been classified — with their category:/domain: tags — are searchable via
search_kb(kind="notes"), closing the loop: notes-api → classifier → tags →
knowledge base → kb-agent.

The collection is rebuilt from scratch each run, so deleted/renamed KB files
don't leave stale chunks behind.

Usage:
    uv run python scripts/index.py
"""

from __future__ import annotations

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

# Roughly target this many characters per chunk before starting a new one.
MAX_CHUNK_CHARS = 1200

console = Console()


def chunk_markdown(text: str) -> list[str]:
    """Split Markdown into chunks, breaking on headings and capping size.

    Each heading starts a new chunk, and any section that grows past
    MAX_CHUNK_CHARS is flushed into its own chunk so no single chunk greatly
    exceeds the budget.

    Args:
        text: The full Markdown text of one KB file.

    Returns:
        The non-empty, stripped chunks in document order.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            joined = "\n".join(current).strip()
            if joined:
                chunks.append(joined)
            current = []
            current_len = 0

    for line in text.splitlines():
        is_heading = line.startswith("#")
        # Start a fresh chunk at a heading boundary or when we're over budget.
        if (is_heading and current) or current_len >= MAX_CHUNK_CHARS:
            flush()
        current.append(line)
        current_len += len(line) + 1

    flush()
    return chunks


def notes_dirs() -> list[Path]:
    """External directories of hand-written notes to index alongside kb/.

    Configured under ``notes_dirs`` in projects.yaml. These live outside this
    repo (and outside git) on purpose; we only read them at index time.
    """
    projects_file = REPO_ROOT / "projects.yaml"
    if not projects_file.exists():
        return []
    config = yaml.safe_load(projects_file.read_text(encoding="utf-8")) or {}
    return [Path(p) for p in config.get("notes_dirs", [])]


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
        text = _note_to_markdown(note)
        for i, chunk in enumerate(chunk_markdown(text)):
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
    for i, chunk in enumerate(chunk_markdown(text)):
        documents.append(chunk)
        metadatas.append({"source": source, "kind": kind, "name": md_file.stem})
        ids.append(f"{kind}/{md_file.stem}#{i}")


def collect_documents() -> tuple[list[str], list[dict], list[str]]:
    """Build the parallel arrays ChromaDB's add() expects, one entry per chunk.

    Walks the in-repo kb/ tree (kind = parent folder, e.g. ``projects`` /
    ``libraries``) plus any external ``notes_dirs`` from projects.yaml
    (kind = ``notes``) and live notes from the notes-api service
    (kind = ``notes``). Each metadata dict carries ``source``, ``kind``, and
    ``name``.

    Returns:
        A ``(documents, metadatas, ids)`` tuple of equal-length lists.
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

    for notes_dir in notes_dirs():
        if not notes_dir.exists():
            console.print(f"[yellow]notes_dir not found, skipping: {notes_dir}[/yellow]")
            continue
        for md_file in sorted(notes_dir.rglob("*.md")):
            _add_file(
                md_file,
                "notes",
                f"{notes_dir.name}/{md_file.name}",
                documents,
                metadatas,
                ids,
            )

    # Pull live notes from the notes-api (closes the "tags → knowledge base" loop).
    api_docs, api_metas, api_ids = collect_notes_from_api()
    documents.extend(api_docs)
    metadatas.extend(api_metas)
    ids.extend(api_ids)

    return documents, metadatas, ids


def main() -> None:
    """Rebuild the ChromaDB collection from every chunk under kb/.

    Drops any existing collection first so renamed or deleted KB files don't
    leave stale chunks behind, then prints a one-line summary.
    """
    documents, metadatas, ids = collect_documents()

    if not documents:
        console.print("[yellow]No Markdown files found under kb/. Run ingest.py first.[/yellow]")
        return

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Rebuild from scratch so deletions/renames don't leave stale chunks.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # Collection didn't exist yet — fine.
    collection = client.create_collection(COLLECTION_NAME)

    # Each dict here IS a valid ChromaDB Metadata (Mapping[str, ...]), but
    # `list` is invariant: list[dict] isn't assignable to List[Metadata], so the
    # type checker flags the call. It's correct at runtime; ignore just this arg.
    collection.add(documents=documents, metadatas=metadatas, ids=ids)  # type: ignore[arg-type]

    console.print(
        f"[bold green]Indexed[/bold green] {len(documents)} chunks "
        f"from {len({m['source'] for m in metadatas})} files "
        f"into '{COLLECTION_NAME}' at {CHROMA_DIR.name}/"
    )


if __name__ == "__main__":
    main()
