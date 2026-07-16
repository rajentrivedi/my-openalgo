# AGENTS.md

Agent instructions for OpenAlgo. See also `CLAUDE.md` for deep architectural detail.

## Quick Commands

```bash
# Run app (dev server with auto-reload)
uv run app.py

# Run app (production, Linux only)
uv run gunicorn --worker-class eventlet -w 1 app:app

# Lint + format (Python)
uv run ruff check .
uv run ruff format .

# Frontend lint/build (only when editing React)
cd frontend && npm run lint && npm run build

# Tests
uv run pytest test/ -v
cd frontend && npm test
```

**Never use global Python or `pip`.** Always prefix with `uv run`. Never use `uv pip` for syncing — use `uv sync`.

## Two Independent Versions

1. **Platform version**: `utils/version.py` + `pyproject.toml` line 4. Run `uv sync` after bumping.
2. **SDK pin** (`openalgo==X.Y.Z`): `pyproject.toml` + `requirements.txt` + `requirements-nginx.txt`. Run `uv sync` after bumping.

They are unrelated. Do not confuse them.

## Critical Invariants (Do Not Break)

- **SQLite: NullPool only.** All DB engines use `NullPool` via `database.engine_factory.create_db_engine()`. Never `StaticPool` — it causes concurrency corruption under eventlet.
- **ZeroMQ bus: SUB binds, PUBs connect.** The proxy (`websocket_proxy/server.py`) is the sole ZMQ binder. Publishers (broker adapters, cache invalidation) always `connect()`. Never `bind()` from a publisher.
- **Gunicorn: `-w 1` always.** Flask-SocketIO state is in-process. Multiple workers breaks WebSocket/SocketIO.
- **No `asyncio` in production.** eventlet monkey-patches stdlib; `asyncio.run()`, `async/await` are incompatible. Use eventlet green threads or a separate OS thread.
- **Multi-session login must not tear down the broker feed.** `upsert_auth` in `database/auth_db.py` only triggers ZMQ cache invalidation + pool cleanup when the token *actually changed*. Never remove this gate.

## Frontend Dist CI/CD

`frontend/dist/` is gitignored locally but force-committed to `main` by CI. Production servers do not need Node.js — `git pull` from main brings the latest UI. Only build locally when actively editing React code.

## Pre-commit Hooks

Runs on commit: Ruff (lint + format), Biome (frontend), detect-secrets, trailing whitespace, YAML/JSON checks. Install with `pre-commit install`.

## CI Pipeline

`backend-lint` → `backend-test` → `frontend-lint` → `frontend-build` → `frontend-test` → `frontend-e2e` → `security-scan` → `commit-dist` (main only) → `docker-build` → `docker-manifest`. Lint/format checks are `continue-on-error` due to pre-existing broker module warnings.

## Broker Plugin Pattern

34 brokers in `broker/{name}/`. Each has: `api/auth_api.py`, `api/order_api.py`, `api/data.py`, `api/funds.py`, `mapping/`, `streaming/`, `database/master_contract_db.py`, `plugin.json`. Reference: `broker/zerodha/`, `broker/dhan/`, `broker/angel/`.

## FD Hygiene

Every DB engine/session, file, socket, WebSocket, ZMQ socket, subprocess pipe, thread, and executor is a file descriptor. Use `with` context managers or shared singletons. After changes touching any of these, audit for FD leaks — a long-running single-worker Gunicorn process will crash with "too many open files" if descriptors leak.

## Error Logging

Use `logger.exception()` (never `logger.error()` + manual traceback). Debug issues by reading `log/errors.jsonl` first. Do not use `import traceback` / `traceback.print_exc()` — bypasses centralized logging.

## Docs

Start from `docs/INDEX.md`. All documentation lives in `docs/` as markdown. Edit the source in `docs/` — do not copy content to a second location.
