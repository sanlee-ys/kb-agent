"""Optional OpenTelemetry tracing for the agent's tool-use loop.

The instrumentation in ``agent/agent.py`` is *always* present in the code, but it
runs against the OpenTelemetry **API**, whose default tracer is a no-op that
records nothing and costs nothing. The **SDK** that actually records and exports
spans is configured only when ``KB_AGENT_TRACING`` is set — so a normal run, and
the offline test suite, are unaffected unless you opt in.

Why an agent loop needs this: one ``KBAgent.ask()`` fans out into N model calls
and M tool calls. The questions that decide whether the thing is fast and cheap —
*which tool is slow, where the tokens go, how many loop passes a turn took* — are
invisible without a span per step. That is the "AI observability" item on the
architecture repo's SYS-007 skill map, made concrete on a real agent.

Enable it::

    KB_AGENT_TRACING=1 uv run python agent/agent.py          # spans to stderr
    KB_AGENT_TRACING=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
        uv run python agent/agent.py                          # also to a collector

The OTLP exporter is an optional extra (``pip install 'kb-agent[otlp]'`` /
``uv sync --extra otlp``); the console exporter needs no infrastructure and is
always available. Span attributes follow OpenTelemetry's GenAI semantic
conventions (``gen_ai.*``) where they exist, with a small ``kb_agent.*`` namespace
for loop-specific facts the conventions don't cover.
"""

from __future__ import annotations

import json
import os
import sys

from opentelemetry import trace

_TRACER_NAME = "kb-agent"
_CONFIGURED = False


def _enabled() -> bool:
    """Return whether tracing is switched on via ``KB_AGENT_TRACING``.

    Truthy is "set to anything but an obvious off value", so ``KB_AGENT_TRACING=1``
    and ``KB_AGENT_TRACING=true`` both enable it while ``0``/``false``/``no``/empty
    leave it off.
    """
    return os.environ.get("KB_AGENT_TRACING", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def setup_tracing() -> bool:
    """Configure the OpenTelemetry SDK if tracing is enabled. Idempotent.

    When enabled, installs a console span exporter (writing to stderr, so it never
    pollutes the agent's stdout answer) and, if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
    set and the OTLP extra is installed, an OTLP exporter alongside it. When
    disabled, leaves the API's global no-op provider in place and does nothing.

    Returns:
        True if the SDK is now active, False if tracing was left as the no-op
        default. Safe to call more than once; only the first enabled call
        configures the provider.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return True
    if not _enabled():
        return False

    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "kb-agent"}))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            print(
                "kb-agent: OTEL_EXPORTER_OTLP_ENDPOINT is set but the OTLP exporter "
                "is not installed (uv sync --extra otlp). Using the console exporter "
                "only.",
                file=sys.stderr,
            )

    trace.set_tracer_provider(provider)
    _CONFIGURED = True
    return True


def get_tracer():
    """Return the kb-agent tracer from whatever provider is currently installed.

    Resolves against the global provider at call time, so it is the no-op tracer
    until :func:`setup_tracing` installs a real SDK provider.
    """
    return trace.get_tracer(_TRACER_NAME)


def set_usage_attributes(span, usage) -> None:
    """Copy Anthropic token-usage counts onto ``span`` as GenAI attributes.

    Reads fields defensively (``getattr``), so a usage object missing the cache
    fields — or missing entirely — simply contributes fewer attributes rather than
    raising. Input/output tokens use the standard ``gen_ai.usage.*`` names; the
    cache counts use the same namespace as an Anthropic-specific extension the
    conventions don't yet cover.

    Args:
        span: The span to annotate (caller should guard on ``is_recording()``).
        usage: An Anthropic response ``usage`` object, or None.
    """
    if usage is None:
        return
    for field, key in (
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("cache_read_input_tokens", "gen_ai.usage.cache_read_input_tokens"),
        ("cache_creation_input_tokens", "gen_ai.usage.cache_creation_input_tokens"),
    ):
        value = getattr(usage, field, None)
        if value is not None:
            span.set_attribute(key, value)


def observation_status(observation: str) -> str:
    """Extract the SYS-003 ``status`` from a tool-result observation string.

    Every kb-agent tool returns a SYS-003 observation — a JSON string with a
    ``status`` of ``success``/``warning``/``error``. This reads that field for use
    as a span attribute, returning ``"unknown"`` for anything that isn't a JSON
    object carrying a string status (so a malformed result never breaks tracing).

    Args:
        observation: The tool result string returned by ``execute_tool``.

    Returns:
        The status string, or ``"unknown"``.
    """
    try:
        data = json.loads(observation)
    except (ValueError, TypeError):
        return "unknown"
    if isinstance(data, dict):
        status = data.get("status")
        if isinstance(status, str):
            return status
    return "unknown"
