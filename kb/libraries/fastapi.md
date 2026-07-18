# fastapi
- FastAPI is a modern, high-performance Python web framework for building APIs, based on standard Python type hints and built on top of Starlette (for the web parts) and Pydantic (for data validation).

## What it's for
- Building REST APIs quickly, with automatic request validation, serialization, and error handling derived from type-annotated Python functions.
- Auto-generating interactive API documentation (Swagger UI at `/docs` and ReDoc at `/redoc`) from your route definitions and Pydantic models.
- Building async APIs that need high concurrency (e.g., I/O-bound services calling databases or external APIs), since it natively supports `async def` endpoints.
- Powering microservices, backend-for-frontend layers, and ML model-serving endpoints where a typed, self-documenting API is valuable.

## Gotchas
- Mixing sync and async carelessly: calling blocking (synchronous) code inside an `async def` endpoint blocks the event loop; blocking calls should either be run in a thread pool (e.g., via `run_in_threadpool`) or the endpoint should be defined as a regular `def` (FastAPI runs those in a threadpool automatically).
- Pydantic version differences matter: FastAPI's behavior and syntax for models/validators can differ noticeably between Pydantic v1 and v2, and upgrading one often requires adjusting the other.
