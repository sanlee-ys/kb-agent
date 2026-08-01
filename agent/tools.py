"""Tools the KB agent can call.

Four tools:

  - search_kb(query, kind?, n_results?) — search over the indexed Markdown KB.
    Local. Dense (ChromaDB) by default, with a switchable hybrid dense+BM25 path
    behind ``HYBRID_RETRIEVAL`` — see kb-agent/ADR-010 for the A/B that set that
    default.
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

Canonical record: ``decisions/ADR-001-manual-tool-loop-over-sdk-runner.md``.
(This summary predates the repo having a decisions/ tier at all — the full
record lived here until 2026-07-18. It stays as an inline pointer.)

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

import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import chromadb
import httpx
import yaml
from rank_bm25 import BM25Okapi

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "projects.yaml"
CHROMA_DIR = REPO_ROOT / "chroma_db"
COLLECTION_NAME = "knowledge_base"
KINDS = ("projects", "libraries", "notes")

# --- Retrieval mode (kb-agent/ADR-010) ---------------------------------------
# search_kb can retrieve dense-only (ChromaDB / all-MiniLM-L6-v2) or hybrid
# (dense + BM25, fused with Reciprocal Rank Fusion). Both paths are live and
# switchable; HYBRID_RETRIEVAL is the default when a caller passes no explicit
# `hybrid=`. It is False because the gold-set A/B said so, not because the
# hybrid path is unfinished — see decisions/ADR-010 for the numbers.
HYBRID_RETRIEVAL = False

# RRF constant. 60 is the value from Cormack et al. (2009), where it was tuned
# once and then left alone precisely because the method is insensitive to it;
# keeping the canonical value means the fusion has no knob that was silently
# fitted to this 27-query gold set.
RRF_K = 60

# How deep each leg ranks before fusion. Fusing only the top n_results would
# make RRF a no-op for anything the dense leg already ranked first, so each leg
# offers a wider candidate pool and fusion picks the final n_results out of it.
CANDIDATE_MULTIPLIER = 5
MIN_CANDIDATES = 25

# Word characters only, lowercased: "SYS-003" -> ["sys", "003"] on both the
# query and the corpus side, so hyphenated jargon still matches lexically.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


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


def _tokenize(text: str) -> list[str]:
    """Lowercase a string into BM25 terms. Used for both corpus and query."""
    return _TOKEN_RE.findall(text.lower())


class _LexicalIndex(NamedTuple):
    """An in-memory BM25 view of the whole KB collection.

    Attributes:
        fingerprint: Hash of the (id, text) pairs it was built from — the cache key.
        ids: Chunk ids, in collection order.
        documents: Chunk texts, parallel to ``ids``.
        metadatas: Chunk metadata dicts, parallel to ``ids``.
        position: ``id -> index`` into the parallel lists.
        bm25: The fitted BM25 model over the tokenized ``documents``.
    """

    fingerprint: str
    ids: list[str]
    documents: list[str]
    metadatas: list[dict]
    position: dict[str, int]
    bm25: BM25Okapi


# One cached _LexicalIndex per process, invalidated by content fingerprint.
_LEXICAL_CACHE: _LexicalIndex | None = None


def _corpus_fingerprint(ids: list[str], documents: list[str]) -> str:
    """Hash the (id, text) pairs that define a BM25 index's inputs."""
    digest = hashlib.sha256()
    for chunk_id, document in zip(ids, documents):
        digest.update(chunk_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _lexical_index(collection) -> _LexicalIndex | None:
    """Build (or reuse) the BM25 index over the collection's chunks.

    Reads the corpus back out of ChromaDB with ``collection.get()`` — a read, so
    the CVE-2026-45829 assessment's "embedded client, one local writer, no custom
    embedding_function" premises are untouched. The read is cheap (a few hundred
    chunks) and is what makes the cache *correct*: the fingerprint is computed
    from the text actually stored, so an incremental re-index that changes chunk
    contents without changing the chunk count still invalidates.

    Args:
        collection: The open ChromaDB collection.

    Returns:
        The fitted index, or None when the collection is empty (BM25 is
        undefined on an empty corpus — callers fall back to dense-only).
    """
    global _LEXICAL_CACHE
    snapshot = collection.get(include=["documents", "metadatas"])
    ids = list(snapshot.get("ids") or [])
    documents = list(snapshot.get("documents") or [])
    metadatas = list(snapshot.get("metadatas") or [])
    if not ids or len(documents) != len(ids):
        return None

    fingerprint = _corpus_fingerprint(ids, documents)
    if _LEXICAL_CACHE is None or _LEXICAL_CACHE.fingerprint != fingerprint:
        _LEXICAL_CACHE = _LexicalIndex(
            fingerprint=fingerprint,
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            position={chunk_id: i for i, chunk_id in enumerate(ids)},
            bm25=BM25Okapi([_tokenize(doc) for doc in documents]),
        )
    return _LEXICAL_CACHE


def _lexical_ranking(
    index: _LexicalIndex, query: str, kind: str | None, limit: int
) -> list[str]:
    """Rank chunk ids by BM25 score, highest first.

    Chunks scoring zero (no query term occurs in them) are dropped rather than
    ranked: BM25 orders them arbitrarily, and feeding that arbitrary tail into
    RRF would add rank signal where there is no lexical evidence at all.

    The ``kind`` filter is applied *after* scoring, so term statistics (IDF,
    average document length) stay corpus-wide and a filtered query returns the
    same relative order as the unfiltered one, minus the excluded kinds. That
    mirrors the dense leg, whose embeddings are likewise kind-independent.

    Args:
        index: The fitted lexical index.
        query: The user query.
        kind: A validated kind to restrict to, or None for all kinds.
        limit: Maximum number of ids to return.

    Returns:
        Up to ``limit`` chunk ids in descending BM25 order; ties broken by
        collection order so the ranking is deterministic.
    """
    scores = index.bm25.get_scores(_tokenize(query))
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    ranked: list[str] = []
    for i in order:
        if scores[i] <= 0:
            break  # sorted descending — everything after this is also zero
        if kind is not None and index.metadatas[i].get("kind") != kind:
            continue
        ranked.append(index.ids[i])
        if len(ranked) >= limit:
            break
    return ranked


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Fuse ranked id lists into one by Reciprocal Rank Fusion.

    Each list contributes ``1 / (k + rank)`` to every id it ranks (rank is
    1-based); ids are then sorted by total score. RRF fuses *ranks*, not scores,
    which is the whole reason it suits this problem: a cosine distance and a BM25
    score have no common scale, and any weighted blend of them would need a
    normalization constant fitted on the same 27 queries it is being judged by.

    Args:
        rankings: One ranked list of chunk ids per retrieval leg. An id absent
            from a leg simply earns nothing from it.
        k: The RRF damping constant.

    Returns:
        All ids seen in any leg, best first. Ties are broken by first appearance
        (earliest leg, then earliest rank within it), so the result is a pure
        function of the inputs.
    """
    scores: dict[str, float] = {}
    first_seen: list[str] = []
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id not in scores:
                scores[chunk_id] = 0.0
                first_seen.append(chunk_id)
            scores[chunk_id] += 1.0 / (k + rank)
    # sorted() is stable, so equal scores keep first_seen order.
    return sorted(first_seen, key=lambda chunk_id: -scores[chunk_id])


def search_kb(
    query: str,
    kind: str | None = None,
    n_results: int = 5,
    hybrid: bool | None = None,
) -> str:
    """Semantically search the knowledge base for relevant chunks.

    Two retrieval modes share this entry point (kb-agent/ADR-010). Dense-only
    queries the ChromaDB collection and returns its ranking. Hybrid additionally
    ranks the same chunk corpus with BM25 and fuses the two rankings with RRF.
    Both honor ``kind`` identically, and both return the same chunk shape.

    Args:
        query: What to search for, in natural language.
        kind: Optional filter — ``"projects"``, ``"libraries"``, or ``"notes"``.
            Any other value (or None) searches all kinds.
        n_results: Maximum number of chunks to return.
        hybrid: Force the retrieval mode. None (the default, and what every
            model-facing caller passes) uses the ``HYBRID_RETRIEVAL`` default.
            Deliberately absent from the ``TOOLS`` schema: it is an evaluation
            and experiment hook, not something for the model to choose.

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

    use_hybrid = HYBRID_RETRIEVAL if hybrid is None else hybrid
    kind_filter = kind if kind in KINDS else None
    where = {"kind": kind_filter} if kind_filter else None

    # Dense-only needs exactly n_results; hybrid needs a deeper pool to fuse.
    depth = (
        max(n_results * CANDIDATE_MULTIPLIER, MIN_CANDIDATES) if use_hybrid else n_results
    )
    results = collection.query(query_texts=[query], n_results=depth, where=where)

    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    if use_hybrid:
        index = _lexical_index(collection)
        if index is not None:
            lexical = _lexical_ranking(index, query, kind_filter, depth)
            by_id = dict(zip(ids, zip(documents, metadatas)))
            for chunk_id in lexical:
                if chunk_id not in by_id:
                    i = index.position[chunk_id]
                    by_id[chunk_id] = (index.documents[i], index.metadatas[i])
            fused = reciprocal_rank_fusion([list(ids), lexical])
            documents = [by_id[chunk_id][0] for chunk_id in fused]
            metadatas = [by_id[chunk_id][1] for chunk_id in fused]

    documents = documents[:n_results]
    metadatas = metadatas[:n_results]
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

# This consumer's belief about the frozen /classify response shape (SYS-004).
# It lives in one place so the runtime check and the cross-repo contract check
# (scripts/check_classify_contract.py) can never disagree with each other — the
# whole failure this guard exists to prevent is two copies of a shape drifting
# apart unnoticed. The provider publishes the authoritative list at
# contracts/classify-response.schema.json in its own repo; that script asserts
# this tuple still matches it.
CLASSIFY_REQUIRED_FIELDS = ("category", "operational_domain", "region")


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
        ``category``, ``operational_domain`` and ``region`` labels. Every failure
        path — no endpoint, unreachable service, transport error, or non-200 —
        returns an error observation with root-cause, remediation, and a stop
        condition.
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
        for key in CLASSIFY_REQUIRED_FIELDS
        if not isinstance(data, dict) or key not in data
    ]
    if missing:
        return _problem(
            "error",
            f"The {CLASSIFIER_PROJECT} service returned a 200 response that "
            f"violates the frozen /classify contract (SYS-004): expected a JSON "
            f"object with 'category', 'operational_domain' and 'region', missing "
            f"{', '.join(missing)}.",
            [
                f"Service body: {response.text}",
                "This is a service-side contract violation, not a usage problem. "
                "Stop and report it; do not retry unchanged.",
            ],
        )

    return _success(
        f"Classified as {data['category']} / {data['operational_domain']} / "
        f"{data['region']}.",
        payload={
            "category": data["category"],
            "operational_domain": data["operational_domain"],
            "region": data["region"],
        },
        source=f"{CLASSIFIER_PROJECT} service, {url}",
    )


NOTES_PROJECT = "notes-api"

# The GET /notes fields this consumer actually reads (SYS-006). notes-api returns
# more than these and that is fine — the read contract is deliberately open, so an
# added provider field is backward-compatible. What breaks this tool is one of
# THESE disappearing, and because they are read with .get() the failure is silent:
# notes come back with None values and nothing looks broken until an answer is
# wrong. scripts/check_notes_contract.py asserts they still exist upstream.
NOTES_READ_FIELDS = ("id", "title", "content", "tags")


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
        {field: n.get(field, [] if field == "tags" else None) for field in NOTES_READ_FIELDS}
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
