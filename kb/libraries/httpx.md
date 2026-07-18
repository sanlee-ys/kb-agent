# httpx

- `httpx` is a fully featured HTTP client library for Python that supports both synchronous and asynchronous requests, with an API modeled closely on the popular `requests` library.

## What it's for
- Making HTTP requests (GET, POST, etc.) in both sync and async Python code using the same familiar API.
- Async web scraping, API clients, or microservice communication using `async`/`await` with `httpx.AsyncClient`.
- Support for HTTP/2 and connection pooling for more efficient, modern network communication.
- Testing web applications (e.g., ASGI/WSGI apps) directly in-process without needing a running server, via its `Client(transport=...)` or `ASGITransport`/`WSGITransport` support.

## Gotchas
- Unlike `requests`, `httpx.Client` and `httpx.AsyncClient` instances should generally be reused (or used as context managers) rather than creating a new client per request, for proper connection pooling—creating one-off clients repeatedly negates performance benefits and can leak resources.
- Async usage requires using `AsyncClient` and `await`ing calls; mixing sync `httpx.get()` calls in async code (or vice versa) can lead to blocking behavior or errors, so be mindful of which client type fits your context.
