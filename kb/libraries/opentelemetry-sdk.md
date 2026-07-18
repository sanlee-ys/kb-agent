# opentelemetry-sdk
- The official Python implementation of the OpenTelemetry SDK, providing the concrete tracing, metrics, and logging functionality that fulfills the `opentelemetry-api` interfaces.

## What it's for
- Instrumenting Python applications to collect distributed traces, metrics, and logs for observability.
- Configuring exporters (e.g., OTLP, Jaeger, Zipkin, console) to send telemetry data to backends or collectors.
- Setting up processors (batch or simple span processors) and samplers to control how and when telemetry data is generated and exported.
- Defining resource attributes (service name, version, environment) that get attached to all emitted telemetry.

## Gotchas
- Installing only `opentelemetry-api` is not enough to actually export data—you need `opentelemetry-sdk` plus a specific exporter package (e.g., `opentelemetry-exporter-otlp`), and forgetting this is a common source of "nothing shows up" confusion.
- If you don't explicitly set a `TracerProvider`/`MeterProvider` via the SDK (using `trace.set_tracer_provider(...)`), the API defaults to no-op implementations, so instrumentation silently does nothing.
