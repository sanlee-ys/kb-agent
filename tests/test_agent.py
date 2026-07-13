"""Tests for the agent loop's search_result rendering — pure, no Anthropic API calls.

``_search_kb_tool_result_content`` turns a search_kb SYS-003 observation into the
Anthropic ``search_result`` content blocks that give KB answers automatic source
citations. These tests pin both the success path (structured blocks, citations on)
and the pass-through paths (non-success observations and unparseable input stay the
raw string, preserving the SYS-003 recovery contract).
"""

from __future__ import annotations

import json

from agent.agent import (
    CACHE_CONTROL,
    _cached_system,
    _messages_with_cache_marker,
    _search_kb_tool_result_content,
)
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


def test_cached_system_wraps_prompt_with_breakpoint():
    blocks = _cached_system("hello system")
    # One text block carrying the prompt and exactly one ephemeral cache breakpoint.
    assert blocks == [
        {"type": "text", "text": "hello system", "cache_control": CACHE_CONTROL}
    ]


def test_cache_marker_promotes_string_content_to_marked_block():
    messages = [{"role": "user", "content": "what is spaCy?"}]
    marked = _messages_with_cache_marker(messages)
    # The bare string is promoted to a text block carrying the breakpoint...
    assert marked[-1]["content"] == [
        {"type": "text", "text": "what is spaCy?", "cache_control": CACHE_CONTROL}
    ]
    # ...without mutating the caller's history (stays a clean transcript).
    assert messages[-1]["content"] == "what is spaCy?"


def test_cache_marker_marks_last_block_of_tool_result_batch():
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [{"type": "text", "text": "thinking"}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                {"type": "tool_result", "tool_use_id": "b", "content": "two"},
            ],
        },
    ]
    marked = _messages_with_cache_marker(messages)
    last_blocks = marked[-1]["content"]
    # Only the final block gets the breakpoint; earlier blocks are untouched.
    assert "cache_control" not in last_blocks[0]
    assert last_blocks[-1]["cache_control"] == CACHE_CONTROL
    assert last_blocks[-1]["tool_use_id"] == "b"
    # Earlier messages and the caller's list are left unmutated.
    assert "cache_control" not in messages[-1]["content"][-1]
    assert marked[0] is messages[0]


def test_cache_marker_stays_within_two_breakpoints():
    # System block = 1 breakpoint; the marker adds exactly 1 more, well under the
    # 4-per-request cap, regardless of how long the transcript grows.
    messages = [{"role": "user", "content": [{"type": "text", "text": f"m{i}"} for i in range(20)]}]
    marked = _messages_with_cache_marker(messages)
    n_marks = sum(
        1 for b in marked[-1]["content"] if b.get("cache_control") == CACHE_CONTROL
    )
    assert n_marks == 1


def test_cache_marker_empty_messages_unchanged():
    assert _messages_with_cache_marker([]) == []


def test_not_indexed_search_kb_result_passes_through(tmp_path, monkeypatch):
    # End-to-end with the real tool: point CHROMA_DIR at a missing path so search_kb
    # returns its not-indexed error observation, which must survive the renderer.
    import agent.tools as tools

    monkeypatch.setattr(tools, "CHROMA_DIR", tmp_path / "no_such_index")
    raw = search_kb("anything")
    rendered = _search_kb_tool_result_content(raw)
    assert rendered == raw
    assert json.loads(rendered)["status"] == "error"
