"""Tools the KB agent can call.

Three tools:

  - search_kb(query, kind?, n_results?) — semantic search over the indexed
    Markdown KB (ChromaDB). Local.
  - list_projects() — list the projects tracked in projects.yaml. Local.
  - classify_snippet(text) — classify a defense-news snippet by calling the
    defense-news-classifier's HTTP service. This is the "ecosystem" seam: the
    agent doesn't just *describe* a tracked project, it *drives* one over HTTP.

Each tool is a plain Python function. The JSON schemas the model sees live in
TOOLS, and execute_tool() dispatches a tool-use request to the right function.
Keeping schemas explicit (rather than auto-generated) makes the tool-use loop in
agent.py easy to follow and doesn't depend on the SDK's beta tool runner.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "projects.yaml"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "knowledge_base"


def _get_collection():
    """Open the persistent KB collection.

    Returns:
        The ChromaDB collection, or None if the store directory doesn't exist
        yet or the collection hasn't been created.
    """
    if not CHROMA_DIR.exists():
        return None
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        return None


def search_kb(query: str, kind: str | None = None, n_results: int = 5) -> str:
    """Semantically search the knowledge base for relevant chunks.

    Args:
        query: What to search for, in natural language.
        kind: Optional filter — ``"projects"``, ``"libraries"``, or ``"notes"``.
            Any other value (or None) searches all kinds.
        n_results: Maximum number of chunks to return.

    Returns:
        Matching chunks joined by ``---`` separators, each prefixed with its
        ``[source: ...]`` file — or a plain-language message if the index is
        missing or nothing matched.
    """
    collection = _get_collection()
    if collection is None:
        return "The knowledge base has not been indexed yet. Run scripts/index.py first."

    where = {"kind": kind} if kind in ("projects", "libraries", "notes") else None
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
    """List the projects tracked in projects.yaml.

    Returns:
        A bulleted "name: description" list of tracked projects, or a
        plain-language message if projects.yaml is missing or empty.
    """
    if not PROJECTS_FILE.exists():
        return "No projects.yaml found."
    config = yaml.safe_load(PROJECTS_FILE.read_text(encoding="utf-8")) or {}
    projects = config.get("projects", [])
    if not projects:
        return "No projects are tracked yet."
    lines = [f"- {p['name']}: {p.get('description', '(no description)')}" for p in projects]
    return "Tracked projects:\n" + "\n".join(lines)


CLASSIFIER_PROJECT = "defense-news-classifier"


def _project_endpoint(name: str) -> str | None:
    """Return the configured HTTP base URL for a named project.

    The endpoint lives in projects.yaml (not hardcoded here) so that adding or
    moving a callable service is a config change, not a code change.

    Args:
        name: The project name to look up, as it appears in projects.yaml.

    Returns:
        The project's configured ``endpoint`` base URL, or None if the project
        isn't found or has no endpoint set.
    """
    if not PROJECTS_FILE.exists():
        return None
    config = yaml.safe_load(PROJECTS_FILE.read_text(encoding="utf-8")) or {}
    for project in config.get("projects", []):
        if project.get("name") == name:
            return project.get("endpoint")
    return None


def classify_snippet(text: str) -> str:
    """Classify a defense-news snippet via the classifier's /classify endpoint.

    Routes to the running defense-news-classifier service over HTTP. The seam is
    deliberately HTTP, not a direct import, so the two projects stay decoupled —
    each has its own environment and release cycle.

    Args:
        text: The defense-news snippet to classify.

    Returns:
        The ``category`` and ``operational_domain`` labels plus a ``[source: ...]``
        line on success. On any failure — no endpoint configured, service
        unreachable, or a non-200 response — a plain-language message explaining
        what went wrong (and how to start the service), rather than raising.
    """
    endpoint = _project_endpoint(CLASSIFIER_PROJECT)
    if not endpoint:
        return (
            f"No endpoint is configured for {CLASSIFIER_PROJECT!r} in projects.yaml, "
            "so it can't be called. Add an 'endpoint:' field to its entry."
        )

    url = endpoint.rstrip("/") + "/classify"
    try:
        # The endpoint makes an upstream LLM call, so allow a generous timeout.
        response = httpx.post(url, json={"text": text}, timeout=30.0)
    except httpx.ConnectError:
        return (
            f"The {CLASSIFIER_PROJECT} service isn't reachable at {endpoint}. "
            "Start it from that project's directory with:\n"
            "    uv run --env-file .env uvicorn api:app --app-dir src"
        )
    except httpx.HTTPError as exc:  # timeouts, malformed responses, etc.
        return f"Error calling the {CLASSIFIER_PROJECT} service: {exc}"

    if response.status_code != 200:
        # Surface the service's own error detail so the model can relay it.
        return (
            f"The {CLASSIFIER_PROJECT} service returned HTTP {response.status_code}: "
            f"{response.text}"
        )

    data = response.json()
    return (
        f"category: {data['category']}\n"
        f"operational_domain: {data['operational_domain']}\n"
        f"[source: {CLASSIFIER_PROJECT} service, {url}]"
    )


# JSON schemas exposed to the model. Descriptions are prescriptive about WHEN to
# call each tool, which improves the model's tool-selection accuracy.
TOOLS = [
    {
        "name": "search_kb",
        "description": (
            "Search the personal knowledge base of projects, libraries, and "
            "plain-language concept notes. Call this whenever the user asks about "
            "a tool, library, concept, design decision, or how something was used "
            "in a project."
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
                    "enum": ["projects", "libraries", "notes"],
                    "description": "Optional filter: restrict to projects, libraries, or concept notes.",
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
    {
        "name": "classify_snippet",
        "description": (
            "Classify a short defense-news snippet into a category and an "
            "operational domain by calling the defense-news-classifier service. "
            "Call this when the user wants a news snippet actually labeled or "
            "classified, not just described."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The defense-news snippet to classify.",
                },
            },
            "required": ["text"],
        },
    },
]

# Map tool name -> callable for dispatch.
_DISPATCH = {
    "search_kb": search_kb,
    "list_projects": list_projects,
    "classify_snippet": classify_snippet,
}


def execute_tool(name: str, tool_input: dict) -> str:
    """Run a tool by name with the model-provided input dict.

    Args:
        name: The tool name from the model's tool_use block.
        tool_input: The tool's arguments, passed through as keyword arguments.

    Returns:
        The tool's string result, or an error message if the tool is unknown or
        raised — errors are returned (not raised) so the model can read them and
        adapt on the next turn.
    """
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
    print("\n---\n")
    # If the classifier service isn't running, this prints the "start it with..."
    # message rather than raising — that's the graceful-failure path working.
    print(classify_snippet(
        "The Pentagon awarded a $4.2B contract for 24 F-35 fighters."
    ))
