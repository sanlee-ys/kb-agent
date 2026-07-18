# python-dotenv

- A Python library that reads key-value pairs from a `.env` file and sets them as environment variables, making it easy to manage configuration outside of source code.

## What it's for

- Keeping secrets and configuration (API keys, database URLs, credentials) out of version control by storing them in a local `.env` file instead of hardcoding them.
- Mimicking production environment variables during local development, so code that calls `os.environ` or `os.getenv` works the same way in dev and prod.
- Supporting twelve-factor app style configuration, where settings come from the environment rather than config files baked into the codebase.
- Loading different `.env` files for different environments (e.g., `.env.test`, `.env.production`) via the `dotenv_path` argument.

## Gotchas

- `load_dotenv()` does **not** override existing environment variables by default — if a variable is already set in the shell/OS, the `.env` value is ignored unless you pass `override=True`.
- The `.env` file is often forgotten in deployment or CI pipelines (since it's typically `.gitignore`'d), leading to missing environment variables in those environments if they aren't set another way.
