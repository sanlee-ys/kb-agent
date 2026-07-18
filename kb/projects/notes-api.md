# notes-api

A personal Notes REST API built with Python and FastAPI, supporting CRUD operations, tags, and case-insensitive substring search over notes. Notes can optionally be enriched asynchronously after creation: a background task calls an external `defense-news-classifier` service and writes back predicted `category` and `operational_domain` tags. Originally a Java/Spring Boot exercise, ported to Python to align with the rest of the portfolio stack.

## Tech stack

- **fastapi** — HTTP layer, routing (`/notes`), dependency injection, and `BackgroundTasks` for post-save tag enrichment
- **uvicorn** — ASGI server used to run the FastAPI app
- **pydantic** — request/response validation and schemas (`NoteRequest`, `TagsRequest`, `NoteResponse`)
- **sqlalchemy** — ORM layer for `Note` and `NoteTag` entities, backed by SQLite (default) or PostgreSQL via `DATABASE_URL`
- **httpx** — HTTP client used to call the external classifier service during background enrichment
- **python-dotenv** — loads environment configuration (`DATABASE_URL`, `CLASSIFIER_URL`, `HOST`) from `.env`
- **opentelemetry-api** / **opentelemetry-sdk** — instrumentation/telemetry support for the service

## Notes

_Add design decisions, trade-offs, and rationale here (see also `decisions/ADR-001`, `decisions/ADR-002`)._
