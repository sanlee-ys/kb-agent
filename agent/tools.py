"""Tools the KB agent can call.

Four tools:

  - search_kb(query, kind?, n_results?) — semantic search over the indexed
    Markdown KB (ChromaDB). Local.
  - list_projects() — list the projects tracked in projects.yaml. Local.
  - classify_snippet(text) — classify a defense-news snippet by calling the
    defense-news-classifier's HTTP service. An "ecosystem" seam: the agent
    doesn't just *describe* a tracked project, it *drives* one over HTTP.
  - search_notes(query?, tag?) — read the user's live notes from the notes-api
    service over HTTP. The second ecosystem seam: the agent reads a tracked
    service's own data, not a static stub.

Each tool is a plain Python function. The JSON schemas the model sees live in
TOOLS, and execute_tool() dispatches a tool-use request to the right function.
Keeping schemas explicit (rather than auto-generated) makes the tool-use loop in
agent.py easy to follow and doesn't depend on the SDK's beta tool runner. A
working spike (2026-07-11, against ``anthropic`` 0.116.0) built the alternative
for real — ``client.beta.messages.tool_runner`` with ``@beta_tool``-decorated
functions — and it was rejected on measured grounds: net line count went *up*
(~25 lines of loop removed, ~40 lines of wrapper added, so the "less code"
premise didn't hold), and the decorator's auto-generated schema for
``search_kb``'s ``kind`` silently dropped its enum constraint
(``["projects", "libraries", "notes"]``) — every safe fix either passes an
explicit ``input_schema`` override (defeating the decorator's whole point) or
triples the places that enum is duplicated. Revisit only if the SDK ever lets
you supply an existing schema dict + description without duplication, i.e. once
auto-generation and the single-source-of-truth convention stop being in tension.

Observation contract (architecture/SYS-003)
--------------------------------------------
Every tool returns a JSON string with a consistent shape, so the model can act on
a result by reading fields instead of parsing prose, and so deterministic graders
can check it:

    success -> {"status": "success", "summary": str, "payload": ..., "source": ...}
    problem -> {"status": "warning"|"error", "summary": str, "next_actions": [str]}

JSON (not labeled text) is the wire format because the acceptance gate leans on
cheap deterministic graders — ``json.loads`` + key asserts — and the model reads
it reliably. Success payloads stay lean; recovery guidance (``next_actions``) is
reserved for the warning/error paths where it earns its tokens. Always build
results via ``_success()`` / ``_problem()`` so the shape lives in one place.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import chromadb
import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "projects.yaml"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "knowledge_base"


def _success(summary: str, payload, source) -> str:
    """Build a success observation conforming to the SYS-003 tool-layer contract.

    Args:
        summary: One-line description of what happened.
        payload: The actual result (chunks, labels, project list, ...).
        source: Provenance the model can cite — a path, URL, or list of them.

    Returns:
        A JSON string with ``status="success"`` plus ``summary``/``payload``/``source``.
    """
    return json.dumps(
        {"status": "success", "summary": summary, "payload": payload, "source": source},
        ensure_ascii=False,
    )


def _problem(status: str, summary: str, next_actions: list[str]) -> str:
    """Build a warning/error observation carrying recovery guidance (SYS-003).

    Args:
        status: ``"warning"`` (recoverable / empty result) or ``"error"`` (failed).
        summary: One-line root-cause description.
        next_actions: Concrete follow-ups — remediation steps and, where looping
            is a risk, an explicit stop condition.

    Returns:
        A JSON string with ``status``/``summary``/``next_actions``.
    """
    return json.dumps(
        {"status": status, "summary": summary, "next_actions": next_actions},
        ensure_ascii=False,
    )


def _get_collection():
    """Open the persistent KB collection.

    Returns:
        The ChromaDB collection, or None if the store directory doesn't exist
        yet or the collection hasn't been created.
    """
    # Embedded PersistentClient only — never HttpClient/server mode. This is load-
    # bearing for CVE-2026-45829's risk assessment; see docs/notes/
    # chromadb-cve-2026-45829-assessment.md before changing how this client is opened.
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
        A SYS-003 observation (JSON string). On success, ``payload`` is a list of
        ``{"source", "text"}`` chunks and ``source`` lists their files. On the
        not-indexed or no-match paths, a warning/error with recovery guidance.
    """
    collection = _get_collection()
    if collection is None:
        return _problem(
            "error",
            "The knowledge base has not been indexed yet.",
            ["Run scripts/index.py to build the index, then retry this search."],
        )

    where = {"kind": kind} if kind in ("projects", "libraries", "notes") else None
    results = collection.query(query_texts=[query], n_results=n_results, where=where)

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    if not documents:
        next_actions = ["Broaden or rephrase the query."]
        if where is not None:
            next_actions.append(f"Drop the kind={kind!r} filter to search all kinds.")
        return _problem("warning", f"No KB results for {query!r}.", next_actions)

    chunks = [
        {"source": meta["source"], "text": doc}
        for doc, meta in zip(documents, metadatas)
    ]
    return _success(
        f"{len(chunks)} matching chunk(s).",
        payload=chunks,
        source=[c["source"] for c in chunks],
    )


def list_projects() -> str:
    """List the projects tracked in projects.yaml.

    Returns:
        A SYS-003 observation (JSON string). On success, ``payload`` is a list of
        ``{"name", "description"}``; otherwise a warning with recovery guidance.
    """
    if not PROJECTS_FILE.exists():
        return _problem(
            "warning",
            "No projects.yaml found.",
            ["Create projects.yaml at the repo root with a 'projects:' list."],
        )
    config = yaml.safe_load(PROJECTS_FILE.read_text(encoding="utf-8")) or {}
    projects = config.get("projects", [])
    if not projects:
        return _problem(
            "warning",
            "No projects are tracked yet.",
            ["Add entries under 'projects:' in projects.yaml."],
        )
    payload = [
        {"name": p["name"], "description": p.get("description", "(no description)")}
        for p in projects
    ]
    return _success(
        f"{len(payload)} tracked project(s).", payload=payload, source="projects.yaml"
    )


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


def _is_allowed_host(host: str) -> bool:
    """Whether an endpoint host may be called by the cross-repo HTTP seams.

    Loopback only by default (these are the user's own local services). Set
    ``KB_ALLOWED_HOSTS`` (comma-separated hostnames) to widen it without a code
    change if a service ever runs on another host.

    Security note: widening this allowlist raises the tool-seam threat model's
    severity ceiling (exfiltration re-enters scope) — see
    ``docs/notes/tool-seam-threat-model.md`` and ``architecture/SYS-010`` rule 3
    before doing so.
    """
    extra = {
        h.strip().lower()
        for h in os.environ.get("KB_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    host_l = host.lower()
    if host_l == "localhost" or host_l in extra:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_endpoint(name: str, endpoint: str) -> str | None:
    """Reject unsafe endpoints before any request is made (SSRF guard).

    Endpoints come from projects.yaml, so a poisoned/edited config could point a
    request at an arbitrary internal host — and these tools send it the snippet
    body / hand its response back to the model. Restrict to well-formed http(s)
    URLs on an allowed (loopback-by-default) host.

    Args:
        name: Project name, for the error message.
        endpoint: The configured base URL to validate.

    Returns:
        ``None`` if the endpoint is safe to call, else a SYS-003 error
        observation (JSON string) explaining why it was rejected.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        reason = "only http and https URLs are permitted"
    elif not parsed.hostname:
        reason = "the URL has no host"
    elif not _is_allowed_host(parsed.hostname):
        reason = (
            f"host {parsed.hostname!r} is not loopback and not in KB_ALLOWED_HOSTS"
        )
    else:
        return None
    return _problem(
        "error",
        f"The endpoint configured for {name!r} ({endpoint!r}) is not allowed: {reason}.",
        [
            "Point this project's 'endpoint:' in projects.yaml at an http(s) URL on "
            "an allowed host (loopback by default; set KB_ALLOWED_HOSTS to widen it).",
            "Then retry. Do not retry unchanged.",
        ],
    )


def classify_snippet(text: str) -> str:
    """Classify a defense-news snippet via the classifier's /classify endpoint.

    Routes to the running defense-news-classifier service over HTTP. The seam is
    deliberately HTTP, not a direct import, so the two projects stay decoupled —
    each has its own environment and release cycle. As the only tool that crosses
    the network, it carries the fullest error-recovery guidance (SYS-003).

    Args:
        text: The defense-news snippet to classify.

    Returns:
        A SYS-003 observation (JSON string). On success, ``payload`` holds the
        ``category`` and ``operational_domain`` labels. Every failure path — no
        endpoint, unreachable service, transport error, or non-200 — returns an
        error observation with root-cause, remediation, and a stop condition.
    """
    endpoint = _project_endpoint(CLASSIFIER_PROJECT)
    if not endpoint:
        return _problem(
            "error",
            f"No endpoint is configured for {CLASSIFIER_PROJECT!r} in projects.yaml.",
            [
                f"Add an 'endpoint:' field to the {CLASSIFIER_PROJECT!r} entry in "
                "projects.yaml, then retry.",
            ],
        )

    invalid = _validate_endpoint(CLASSIFIER_PROJECT, endpoint)
    if invalid:
        return invalid

    url = endpoint.rstrip("/") + "/classify"
    try:
        # The endpoint makes an upstream LLM call, so allow a generous timeout.
        response = httpx.post(url, json={"text": text}, timeout=30.0)
    except httpx.ConnectError:
        return _problem(
            "error",
            f"The {CLASSIFIER_PROJECT} service isn't reachable at {endpoint}.",
            [
                "Start it from that project's directory: "
                "uv run --env-file .env uvicorn api:app --app-dir src "
                "--host 127.0.0.1 --port 8000",
                "Then retry classify_snippet. If it's still unreachable after "
                "starting, stop and tell the user rather than retrying further.",
            ],
        )
    except httpx.HTTPError as exc:  # timeouts, malformed responses, etc.
        return _problem(
            "error",
            f"Error calling the {CLASSIFIER_PROJECT} service: {exc}",
            [
                "Retry once in case it was transient.",
                "If it fails again, stop and report the error rather than looping.",
            ],
        )

    if response.status_code != 200:
        # Surface the service's own error detail so the model can relay it.
        return _problem(
            "error",
            f"The {CLASSIFIER_PROJECT} service returned HTTP {response.status_code}.",
            [
                f"Service detail: {response.text}",
                "Fix the request or the service, then retry. Do not retry unchanged.",
            ],
        )

    # A 200 is necessary but not sufficient: the body must honor the frozen
    # /classify contract (SYS-004) — a JSON object carrying both `category` and
    # `operational_domain`. Parse defensively so a malformed/contract-violating
    # 200 surfaces as a clean error observation instead of a raw KeyError/
    # ValueError escaping the tool.
    try:
        data = response.json()
    except ValueError:
        return _problem(
            "error",
            f"The {CLASSIFIER_PROJECT} service returned HTTP 200 with a body that "
            "isn't valid JSON, violating the frozen /classify contract (SYS-004).",
            [
                f"Service body: {response.text}",
                "This is a service-side contract violation, not a usage problem. "
                "Stop and report it; do not retry unchanged.",
            ],
        )

    missing = [
        key
        for key in ("category", "operational_domain")
        if not isinstance(data, dict) or key not in data
    ]
    if missing:
        return _problem(
            "error",
            f"The {CLASSIFIER_PROJECT} service returned a 200 response that "
            f"violates the frozen /classify contract (SYS-004): expected a JSON "
            f"object with 'category' and 'operational_domain', missing "
            f"{', '.join(missing)}.",
            [
                f"Service body: {response.text}",
                "This is a service-side contract violation, not a usage problem. "
                "Stop and report it; do not retry unchanged.",
            ],
        )

    return _success(
        f"Classified as {data['category']} / {data['operational_domain']}.",
        payload={
            "category": data["category"],
            "operational_domain": data["operational_domain"],
        },
        source=f"{CLASSIFIER_PROJECT} service, {url}",
    )


NOTES_PROJECT = "notes-api"


def search_notes(query: str | None = None, tag: str | None = None) -> str:
    """Search the user's live notes via the notes-api service's GET /notes endpoint.

    The second cross-repo seam (alongside classify_snippet): the agent reads the
    user's notes from the service that *owns* them, over HTTP, rather than from a
    static KB stub. Deliberately HTTP, not a direct import or a shared DB, so the
    repos stay decoupled. Base URL comes from projects.yaml, not hardcoded.

    Args:
        query: Optional free text to match in a note's title/content (notes-api's
            ``?q=``). Omit to not filter by text.
        tag: Optional exact tag to require (notes-api's ``?tag=``), e.g. a
            ``category:``/``domain:`` label. With neither argument, lists all notes.

    Returns:
        A SYS-003 observation (JSON string). On success, ``payload`` is a list of
        ``{"id", "title", "content", "tags"}`` notes and ``source`` is the service
        URL. An empty result is a warning; every failure path — no endpoint,
        unreachable, transport error, non-200, non-JSON, a non-array body, or an
        array with non-note elements — returns an error observation with root-cause,
        remediation, and a stop condition.
    """
    endpoint = _project_endpoint(NOTES_PROJECT)
    if not endpoint:
        return _problem(
            "error",
            f"No endpoint is configured for {NOTES_PROJECT!r} in projects.yaml.",
            [
                f"Add an 'endpoint:' field to the {NOTES_PROJECT!r} entry in "
                "projects.yaml, then retry.",
            ],
        )

    invalid = _validate_endpoint(NOTES_PROJECT, endpoint)
    if invalid:
        return invalid

    url = endpoint.rstrip("/") + "/notes"
    params: dict[str, str] = {}
    if query:
        params["q"] = query
    if tag:
        params["tag"] = tag

    try:
        # A plain DB-backed read (no LLM), so a short timeout is appropriate.
        response = httpx.get(url, params=params, timeout=10.0)
    except httpx.ConnectError:
        return _problem(
            "error",
            f"The {NOTES_PROJECT} service isn't reachable at {endpoint}.",
            [
                "Start it from that project's directory: "
                "uvicorn notes_api.main:app --port 8081 "
                "(it serves on http://localhost:8081).",
                "Then retry search_notes. If it's still unreachable after starting, "
                "stop and tell the user rather than retrying further.",
            ],
        )
    except httpx.HTTPError as exc:  # timeouts, malformed responses, etc.
        return _problem(
            "error",
            f"Error calling the {NOTES_PROJECT} service: {exc}",
            [
                "Retry once in case it was transient.",
                "If it fails again, stop and report the error rather than looping.",
            ],
        )

    if response.status_code != 200:
        return _problem(
            "error",
            f"The {NOTES_PROJECT} service returned HTTP {response.status_code}.",
            [
                f"Service detail: {response.text}",
                "Fix the request or the service, then retry. Do not retry unchanged.",
            ],
        )

    # A 200 must carry a JSON array of notes. Parse defensively so a malformed body
    # surfaces as a clean error observation instead of an exception escaping the tool.
    try:
        data = response.json()
    except ValueError:
        return _problem(
            "error",
            f"The {NOTES_PROJECT} service returned HTTP 200 with a body that isn't "
            "valid JSON.",
            [
                f"Service body: {response.text}",
                "This is a service-side problem, not a usage problem. Stop and "
                "report it; do not retry unchanged.",
            ],
        )

    if not isinstance(data, list):
        return _problem(
            "error",
            f"The {NOTES_PROJECT} service returned a 200 whose body is not the "
            "expected JSON array of notes.",
            [
                f"Service body: {response.text}",
                "This is a service-side contract problem. Stop and report it; do "
                "not retry unchanged.",
            ],
        )

    # Every element must be a note object. Don't drop non-objects silently: if the
    # array holds anything that isn't a dict, that's a malformed body — surface it as
    # a contract problem rather than collapsing to an empty "success".
    note_objs = [n for n in data if isinstance(n, dict)]
    if len(note_objs) != len(data):
        return _problem(
            "error",
            f"The {NOTES_PROJECT} service returned a 200 array with "
            f"{len(data) - len(note_objs)} element(s) that aren't note objects.",
            [
                f"Service body: {response.text}",
                "This is a service-side contract problem. Stop and report it; do "
                "not retry unchanged.",
            ],
        )

    # Decide emptiness AFTER validating the elements, so "no matches" is a genuine
    # empty result — not a body we silently filtered down to nothing.
    if not note_objs:
        next_actions = ["Broaden or rephrase the query, or omit filters to list all notes."]
        if tag:
            next_actions.append(f"Drop the tag={tag!r} filter.")
        return _problem("warning", "No notes matched the given filters.", next_actions)

    payload = [
        {
            "id": n.get("id"),
            "title": n.get("title"),
            "content": n.get("content"),
            "tags": n.get("tags", []),
        }
        for n in note_objs
    ]
    return _success(
        f"{len(payload)} matching note(s).",
        payload=payload,
        source=f"{NOTES_PROJECT} service, {url}",
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
                    "description": (
                        "Optional filter: restrict to projects, libraries, "
                        "or concept notes."
                    ),
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
    {
        "name": "search_notes",
        "description": (
            "Search the user's live notes in the notes-api service. Call this when "
            "the user asks about their own notes — to find notes on a topic, filter "
            "by a tag, or list what notes exist. Returns matching notes (title, "
            "content, tags) from the running service, not the static KB stubs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free text to match in a note's title/content.",
                },
                "tag": {
                    "type": "string",
                    "description": "Exact tag to require (e.g. a category:/domain: label).",
                },
            },
        },
    },
]

# Map tool name -> callable for dispatch.
_DISPATCH = {
    "search_kb": search_kb,
    "list_projects": list_projects,
    "classify_snippet": classify_snippet,
    "search_notes": search_notes,
}


def execute_tool(name: str, tool_input: dict) -> str:
    """Run a tool by name with the model-provided input dict.

    Args:
        name: The tool name from the model's tool_use block.
        tool_input: The tool's arguments, passed through as keyword arguments.

    Returns:
        The tool's SYS-003 observation string. Unknown tools and unexpected
        exceptions are returned (not raised) as error observations, so the model
        can read them and adapt on the next turn.
    """
    func = _DISPATCH.get(name)
    if func is None:
        return _problem(
            "error",
            f"Unknown tool {name!r}.",
            [f"Call one of: {', '.join(_DISPATCH)}."],
        )
    try:
        return func(**tool_input)
    except Exception as exc:  # Surface errors back to the model so it can adapt.
        return _problem(
            "error",
            f"The {name} tool raised an unexpected error: {exc}",
            [
                "This is an internal error, not a usage problem. Stop and report "
                "it rather than retrying.",
            ],
        )


if __name__ == "__main__":
    # Quick manual smoke test — pretty-print the observation each tool returns.
    def _show(label: str, raw: str) -> None:
        print(label)
        print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        print()

    _show("list_projects():", list_projects())
    _show("search_kb('what is spaCy used for'):", search_kb("what is spaCy used for"))
    # If the classifier service isn't running, this prints an error observation
    # with next_actions (the "start it with..." path) rather than raising — that's
    # the graceful-failure contract working.
    _show(
        "classify_snippet(...):",
        classify_snippet("The Pentagon awarded a $4.2B contract for 24 F-35 fighters."),
    )
    # If the notes-api service isn't running, this prints an error observation with
    # next_actions (the "start it with uvicorn notes_api.main:app --port 8081" path)
    # rather than raising.
    _show("search_notes('drone'):", search_notes("drone"))
