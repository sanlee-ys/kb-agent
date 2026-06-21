"""Tools the KB agent can call.

Two tools for now, both fully local:

  - search_kb(query, kind?, n_results?) — semantic search over the indexed
    Markdown KB (ChromaDB).
  - list_projects() — list the projects tracked in projects.yaml.

Each tool is a plain Python function. The JSON schemas the model sees live in
TOOLS, and execute_tool() dispatches a tool-use request to the right function.
Keeping schemas explicit (rather than auto-generated) makes the tool-use loop in
agent.py easy to follow and doesn't depend on the SDK's beta tool runner.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "projects.yaml"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "knowledge_base"


def _get_collection():
    """Open the persistent KB collection, or return None if not indexed yet."""
    if not CHROMA_DIR.exists():
        return None
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        return None


def search_kb(query: str, kind: str | None = None, n_results: int = 5) -> str:
    """Semantic search over the KB. Optionally filter by kind ('projects'/'libraries')."""
    collection = _get_collection()
    if collection is None:
        return "The knowledge base has not been indexed yet. Run scripts/index.py first."

    where = {"kind": kind} if kind in ("projects", "libraries") else None
    results = collection.query(query_texts=[query], n_results=n_results, where=where)

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    if not documents:
        return f"No KB results for: {query!r}"

    blocks = []
    for doc, meta in zip(documents, metadatas):
        blocks.append(f"[source: {meta['source']}]\n{doc}")
    return "\n\n---\n\n".join(blocks)


def list_projects() -> str:
    """List tracked projects (name + description) from projects.yaml."""
    if not PROJECTS_FILE.exists():
        return "No projects.yaml found."
    config = yaml.safe_load(PROJECTS_FILE.read_text(encoding="utf-8")) or {}
    projects = config.get("projects", [])
    if not projects:
        return "No projects are tracked yet."
    lines = [f"- {p['name']}: {p.get('description', '(no description)')}" for p in projects]
    return "Tracked projects:\n" + "\n".join(lines)


# JSON schemas exposed to the model. Descriptions are prescriptive about WHEN to
# call each tool, which improves the model's tool-selection accuracy.
TOOLS = [
    {
        "name": "search_kb",
        "description": (
            "Search the personal knowledge base of projects and libraries. "
            "Call this whenever the user asks about a tool, library, design "
            "decision, or how something was used in a project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for, in natural language.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["projects", "libraries"],
                    "description": "Optional filter: only search projects or only libraries.",
                },
                "n_results": {
                    "type": "integer",
                    "description": "How many chunks to return (default 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_projects",
        "description": (
            "List all projects tracked in the knowledge base. Call this when the "
            "user asks what projects exist or which projects use a given library."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

# Map tool name -> callable for dispatch.
_DISPATCH = {
    "search_kb": search_kb,
    "list_projects": list_projects,
}


def execute_tool(name: str, tool_input: dict) -> str:
    """Run a tool by name with the model-provided input dict."""
    func = _DISPATCH.get(name)
    if func is None:
        return f"Error: unknown tool {name!r}."
    try:
        return func(**tool_input)
    except Exception as exc:  # Surface errors back to the model so it can adapt.
        return f"Error running {name}: {exc}"


if __name__ == "__main__":
    # Quick manual smoke test.
    print(list_projects())
    print("\n---\n")
    print(search_kb("what is spaCy used for"))
