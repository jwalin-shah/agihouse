# Architecture Hardening Runtime Notes

## SQLite connection management (services.py)

- Local SQLite reads now go through `SQLiteConnectionManager` and `_run_sqlite_read(...)` in `services.py`.
- Connections are cached per `(db_path, thread_id)` and opened read-only with URI mode.
- Locked-database `sqlite3.OperationalError` cases are retried with backoff and then return the caller-provided empty result with warning logs.
- `close_sqlite_connections()` closes and clears cached connections.

## Server shutdown cleanup (inbox_server.py)

- FastAPI lifespan cleanup calls `close_sqlite_connections()` in `finally`, ensuring pooled SQLite connections are closed on shutdown.

## Poll interval configuration (inbox.py)

- Poll timing is configured from `INBOX_POLL_INTERVAL` via `_poll_interval_from_env()`.
- Invalid/non-positive values fall back to `DEFAULT_POLL_INTERVAL` (`10.0` seconds).
