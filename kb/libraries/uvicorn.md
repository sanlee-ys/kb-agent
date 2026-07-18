# uvicorn
- Uvicorn is a lightning-fast ASGI (Asynchronous Server Gateway Interface) web server implementation for Python, built on `uvloop` and `httptools`.

## What it's for
- Running ASGI-compatible Python web frameworks such as FastAPI, Starlette, and (with adapters) Django in async mode.
- Serving HTTP and WebSocket applications in development and production environments.
- Providing a lightweight, high-performance alternative to WSGI servers for async Python web apps.
- Local development with features like hot-reloading (`--reload`) for rapid iteration.

## Gotchas
- In production, Uvicorn is often run behind a process manager like Gunicorn (using `uvicorn.workers.UvicornWorker`) to handle multiple worker processes, since Uvicorn alone doesn't manage worker scaling or restarts robustly.
- The `--reload` flag is intended for development only; it adds overhead and file-watching behavior unsuitable for production deployments.
