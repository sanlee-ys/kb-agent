"""Embed all kb/*.md files into a local ChromaDB collection.

Reads every Markdown file under kb/projects and kb/libraries, splits each into
section-sized chunks, and stores them in a persistent ChromaDB collection using
ChromaDB's built-in local embedding model (all-MiniLM-L6-v2 — no API key, runs
on your machine).

The collection is rebuilt from scratch each run, so deleted/renamed KB files
don't leave stale chunks behind.

Usage:
    uv run python scripts/index.py
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from rich.console import Console

REPO_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = REPO_ROOT / "kb"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "kb"

# Roughly target this many characters per chunk before starting a new one.
MAX_CHUNK_CHARS = 1200

console = Console()


def chunk_markdown(text: str) -> list[str]:
    """Split Markdown into chunks, breaking on headings and capping size.

    Each top-level/sub heading starts a new chunk; long sections are further
    split on blank lines so no single chunk greatly exceeds MAX_CHUNK_CHARS.
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


def collect_documents() -> tuple[list[str], list[dict], list[str]]:
    """Walk kb/ and return (documents, metadatas, ids) for every chunk."""
    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for md_file in sorted(KB_DIR.rglob("*.md")):
        kind = md_file.parent.name  # "projects" or "libraries"
        name = md_file.stem
        text = md_file.read_text(encoding="utf-8")

        for i, chunk in enumerate(chunk_markdown(text)):
            documents.append(chunk)
            metadatas.append({
                "source": str(md_file.relative_to(REPO_ROOT)),
                "kind": kind,
                "name": name,
            })
            ids.append(f"{kind}/{name}#{i}")

    return documents, metadatas, ids


def main() -> None:
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

    collection.add(documents=documents, metadatas=metadatas, ids=ids)

    console.print(
        f"[bold green]Indexed[/bold green] {len(documents)} chunks "
        f"from {len({m['source'] for m in metadatas})} files "
        f"into '{COLLECTION_NAME}' at {CHROMA_DIR.name}/"
    )


if __name__ == "__main__":
    main()
