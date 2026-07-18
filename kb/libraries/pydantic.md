# pydantic

- Pydantic is a Python library for data validation and settings management using Python type annotations, allowing you to define data schemas as classes and automatically parse, validate, and serialize data.

## What it's for

- Validating and parsing incoming data (e.g., API request bodies, config files, JSON payloads) into strongly-typed Python objects, raising clear errors on invalid data.
- Defining data models with type hints (`BaseModel` subclasses) that auto-generate validation logic, default values, and helpful error messages.
- Serializing models to/from JSON or dicts, which makes it a natural fit for web frameworks like FastAPI that use it for request/response schemas.
- Managing application settings/configuration by loading and validating environment variables or config files (via `BaseSettings`, now in the separate `pydantic-settings` package in v2).

## Gotchas

- **Version 1 vs. Version 2 breaking changes**: Pydantic v2 (a major rewrite for performance, using a Rust core) changed a lot of APIs (e.g., `.dict()` → `.model_dump()`, `Config` class → `model_config`, validators syntax), so code and tutorials written for v1 often don't work unchanged in v2.
- **Mutable default handling and coercion surprises**: Pydantic historically coerces types more liberally than you might expect (e.g., converting a numeric string to an int), which can silently accept "wrong" input unless you use stricter types or strict mode.
