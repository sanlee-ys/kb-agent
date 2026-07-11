"""Tests for the agent loop's search_result rendering — pure, no Anthropic API calls.

``_search_kb_tool_result_content`` turns a search_kb SYS-003 observation into the
Anthropic ``search_result`` content blocks that give KB answers automatic source
citations. These tests pin both the success path (structured blocks, citations on)
and the pass-through paths (non-success observations and unparseable input stay the
raw string, preserving the SYS-003 recovery contract).
"""

from __future__ import annotations

import json

from agent.agent import _search_kb_tool_result_content
from agent.tools import search_kb


def _success(chunks) -> str:
    """Build a search_kb-shaped success observation string for the given chunks."""
    return json.dumps(
        {
            "status": "success",
            "summary": f"{len(chunks)} matching chunk(s).",
            "payload": chunks,
            "source": [c["source"] for c in chunks],
        }
    )


def test_success_becomes_search_result_blocks():
    obs = _success(
        [
            {"source": "kb/libraries/spacy.md", "text": "spaCy is an NLP library."},
            {"source": "kb/projects/kb-agent.md", "text": "A personal RAG agent."},
        ]
    )
    blocks = _search_kb_tool_result_content(obs)
    assert isinstance(blocks, list) and len(blocks) == 2
    assert all(b["type"] == "search_result" for b in blocks)
    # Every block carries source + a derived title, and enables citations.
    assert blocks[0]["source"] == "kb/libraries/spacy.md"
    assert blocks[0]["title"] == "spacy"  # derived from the path stem
    assert blocks[0]["content"] == [{"type": "text", "text": "spaCy is an NLP library."}]
    assert blocks[0]["citations"] == {"enabled": True}
    assert blocks[1]["title"] == "kb-agent"


def test_citations_enabled_on_every_block():
    obs = _success([{"source": "kb/notes/rag.md", "text": "Retrieval-augmented generation."}])
    blocks = _search_kb_tool_result_content(obs)
    # Citations are all-or-nothing per request; every emitted block must opt in.
    assert all(b["citations"] == {"enabled": True} for b in blocks)


def test_error_observation_passes_through_unchanged():
    err = json.dumps(
        {
            "status": "error",
            "summary": "The knowledge base has not been indexed yet.",
            "next_actions": ["Run scripts/index.py to build the index, then retry."],
        }
    )
    # Non-success stays the raw string so the model still follows next_actions.
    assert _search_kb_tool_result_content(err) == err


def test_warning_observation_passes_through_unchanged():
    warn = json.dumps(
        {
            "status": "warning",
            "summary": "No KB results for 'nonsense'.",
            "next_actions": ["Broaden or rephrase the query."],
        }
    )
    assert _search_kb_tool_result_content(warn) == warn


def test_non_json_passes_through_unchanged():
    assert _search_kb_tool_result_content("not json at all") == "not json at all"


def test_success_with_empty_text_chunk_is_skipped():
    # A chunk with empty text can't be a valid search_result block (content must be
    # non-empty); with no usable chunks left, fall back to the raw observation.
    obs = _success([{"source": "kb/notes/empty.md", "text": ""}])
    assert _search_kb_tool_result_content(obs) == obs


def test_not_indexed_search_kb_result_passes_through(tmp_path, monkeypatch):
    # End-to-end with the real tool: point CHROMA_DIR at a missing path so search_kb
    # returns its not-indexed error observation, which must survive the renderer.
    import agent.tools as tools

    monkeypatch.setattr(tools, "CHROMA_DIR", tmp_path / "no_such_index")
    raw = search_kb("anything")
    rendered = _search_kb_tool_result_content(raw)
    assert rendered == raw
    assert json.loads(rendered)["status"] == "error"
