# opentelemetry-api

The `opentelemetry-api` package provides the vendor-neutral interfaces, data types, and no-op default implementations that make up the OpenTelemetry specification for Python, used for instrumenting applications to produce traces, metrics, and logs.

## What it's for
- Writing instrumentation code (in libraries or apps) against a stable API without depending on a specific tracing/metrics backend.
- Defining and manipulating spans, trace context, baggage, and metric instruments (counters, histograms, etc.) in a standardized way.
- Enabling library authors to add OpenTelemetry support without forcing a concrete SDK dependency on their users.
- Propagating context across process/service boundaries via standardized propagators (e.g., W3C Trace Context).

## Gotchas
- Installing only `opentelemetry-api` gives you no-op behavior by default—you also need `opentelemetry-sdk` (and an exporter) configured for telemetry to actually be recorded/exported.
- API and SDK versions must be kept in close alignment; mismatched versions between `opentelemetry-api`, `opentelemetry-sdk`, and instrumentation packages can cause runtime errors or silently missing telemetry.
