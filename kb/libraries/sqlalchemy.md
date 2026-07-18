# sqlalchemy

- SQLAlchemy is a Python SQL toolkit and Object-Relational Mapping (ORM) library that provides tools for interacting with relational databases using either raw SQL constructs or Python objects.

## What it's for
- Writing database-agnostic queries using its Core expression language instead of raw SQL strings.
- Mapping Python classes to database tables via the ORM, so you can work with objects instead of rows/columns directly.
- Managing database connections, connection pooling, and transactions across many database backends (PostgreSQL, MySQL, SQLite, Oracle, etc.).
- Handling schema definition/migrations in conjunction with tools like Alembic.

## Gotchas
- Session management is a common source of bugs: objects can become "detached" from a session, and lazy-loaded attributes accessed after the session closes raise `DetachedInstanceError`.
- The ORM's lazy loading can cause the "N+1 query problem" if relationships aren't eagerly loaded (e.g., via `joinedload` or `selectinload`) when iterating over collections.
- SQLAlchemy 1.x and 2.x have notably different APIs/styles (e.g., `Query` vs. the newer `select()`-based unified syntax), so code and tutorials from different versions may not be directly compatible.
