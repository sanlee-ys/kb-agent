"""Tests for the optional OpenTelemetry tracing layer (agent/telemetry.py).

Three things worth pinning, all offline (no API key, no network, no collector):
(1) the SDK only activates when KB_AGENT_TRACING is set, so a normal run and the
rest of the suite stay no-op; (2) observation_status() reads the SYS-003 status
off a tool result and degrades to "unknown" rather than raising; (3) a full
ask() turn emits the expected span tree — turn -> chat -> execute_tool — with the
token and tool-status attributes attached, captured through an in-memory exporter
so no global provider or exporter infrastructure is needed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.telemetry import _enabled, observation_status, setup_tracing


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_enabled_true_for_truthy_values(monkeypatch, value):
    monkeypatch.setenv("KB_AGENT_TRACING", value)
    assert _enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "  "])
def test_enabled_false_for_off_values(monkeypatch, value):
    monkeypatch.setenv("KB_AGENT_TRACING", value)
    assert _enabled() is False


def test_setup_tracing_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("KB_AGENT_TRACING", raising=False)
    import agent.telemetry as telemetry

    # Reset the idempotency latch so a prior enabled run in-process can't mask this.
    monkeypatch.setattr(telemetry, "_CONFIGURED", False)
    assert setup_tracing() is False


def test_observation_status_reads_sys003_status():
    assert observation_status(json.dumps({"status": "success", "payload": []})) == "success"
    assert observation_status(json.dumps({"status": "warning"})) == "warning"
    assert observation_status(json.dumps({"status": "error"})) == "error"


def test_observation_status_unknown_on_garbage():
    assert observation_status("not json at all") == "unknown"
    assert observation_status(json.dumps(["a", "list"])) == "unknown"
    assert observation_status(json.dumps({"no": "status field"})) == "unknown"
    assert observation_status(json.dumps({"status": 500})) == "unknown"  # not a string


def _block(**kw):
    """Build a stand-in Anthropic content block (has .type and any other fields)."""
    return SimpleNamespace(**kw)


def test_ask_emits_span_tree_with_token_and_tool_attributes(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    # A local provider + in-memory exporter, injected via the tracer accessor —
    # no global provider is touched, so this test is isolated from every other.
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    import agent.agent as agent_mod

    monkeypatch.setattr(agent_mod, "get_tracer", lambda: tracer)
    # Deterministic tool result, no real tool or network.
    monkeypatch.setattr(
        agent_mod,
        "execute_tool",
        lambda name, args: json.dumps(
            {"status": "success", "summary": "ok", "payload": [], "source": "x"}
        ),
    )

    # Turn 1: the model asks for a tool. Turn 2: it returns a final answer.
    first = SimpleNamespace(
        stop_reason="tool_use",
        content=[_block(type="tool_use", name="list_projects", input={}, id="t1")],
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=3,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=7,
        ),
    )
    final = SimpleNamespace(
        stop_reason="end_turn",
        content=[_block(type="text", text="done")],
        usage=SimpleNamespace(
            input_tokens=20,
            output_tokens=5,
            cache_read_input_tokens=11,
            cache_creation_input_tokens=0,
        ),
    )
    responses = iter([first, final])

    class _FakeMessages:
        @staticmethod
        def create(**kwargs):
            return next(responses)

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    from agent.agent import KBAgent

    agent = KBAgent()
    agent.client = _FakeClient()

    assert agent.ask("hello") == "done"

    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    # The full tree is present: two model calls, one tool call, one turn.
    assert names.count("chat claude-sonnet-5") == 2
    assert "execute_tool list_projects" in names
    assert "kb_agent.ask" in names

    # Token usage rode onto the first model call from response.usage.
    chat_spans = [s for s in spans if s.name == "chat claude-sonnet-5"]
    assert chat_spans[0].attributes["gen_ai.usage.input_tokens"] == 11
    assert chat_spans[0].attributes["gen_ai.usage.output_tokens"] == 3
    assert chat_spans[0].attributes["gen_ai.usage.cache_creation_input_tokens"] == 7
    assert chat_spans[0].attributes["gen_ai.response.finish_reasons"] == ("tool_use",)

    # The tool span carries the tool name and its parsed SYS-003 status.
    tool_span = next(s for s in spans if s.name == "execute_tool list_projects")
    assert tool_span.attributes["gen_ai.tool.name"] == "list_projects"
    assert tool_span.attributes["kb_agent.tool.status"] == "success"

    # The turn span recorded how many loop passes it took (one tool round + final).
    turn_span = next(s for s in spans if s.name == "kb_agent.ask")
    assert turn_span.attributes["kb_agent.loop.iterations"] == 2
