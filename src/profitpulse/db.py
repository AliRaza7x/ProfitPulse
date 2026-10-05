"""PostgreSQL access: connections, migrations, run tracking and staging merges."""
from __future__ import annotations

import hashlib
import re
import string
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import psycopg2
import psycopg2.extras

from .settings import Settings, get_settings

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def connect(settings: Settings | None = None, **overrides: Any):
    s = settings or get_settings()
    params = dict(host=s.pg_host, port=s.pg_port, dbname=s.pg_db, user=s.pg_user, password=s.pg_password)
    params.update(overrides)
    return psycopg2.connect(**params)


@contextmanager
def transaction(settings: Settings | None = None) -> Iterator[Any]:
    conn = connect(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: Sequence[Any] | None = None, conn=None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def execute(sql: str, params: Sequence[Any] | None = None, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ migrations
def run_migrations(settings: Settings | None = None) -> list[str]:
    """Apply pending SQL migrations in order. Returns the versions applied.

    Applied migrations are checksummed; editing one after the fact is an error
    (add a new migration instead).
    """
    s = settings or get_settings()
    if not _IDENT.match(s.reader_role):
        raise ValueError(f"Unsafe reader role name: {s.reader_role!r}")
    files = sorted(Path(s.migrations_dir).glob("*.sql"))
    applied: list[str] = []
    with transaction(s) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS ops.schema_migrations ("
                "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version, checksum FROM ops.schema_migrations")
            done = dict(cur.fetchall())
        for path in files:
            sql = string.Template(path.read_text(encoding="utf-8")).safe_substitute(READER_ROLE=s.reader_role)
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(
                        f"Migration {path.name} was modified after being applied. Add a new migration instead."
                    )
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO ops.schema_migrations (version, checksum) VALUES (%s, %s)", (path.name, checksum)
                )
            applied.append(path.name)
    return applied


# ------------------------------------------------------------------ run tracking
def start_run(stage: str, dag_id: str | None = None, airflow_run_id: str | None = None,
              details: dict | None = None) -> str:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ops.pipeline_runs (stage, dag_id, airflow_run_id, status, details) "
            "VALUES (%s, %s, %s, 'RUNNING', %s) RETURNING run_id",
            (stage, dag_id, airflow_run_id, psycopg2.extras.Json(details or {})),
        )
        return str(cur.fetchone()[0])


def finish_run(run_id: str, status: str, rows_in: int | None = None, rows_out: int | None = None,
               rows_rejected: int | None = None, details: dict | None = None, error: str | None = None) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ops.pipeline_runs SET status=%s, finished_at=now(), rows_in=%s, rows_out=%s, "
            "rows_rejected=%s, details = details || %s::jsonb, error=%s WHERE run_id=%s",
            (status, rows_in, rows_out, rows_rejected, psycopg2.extras.Json(details or {}), error, run_id),
        )


@contextmanager
def tracked_run(stage: str, **kwargs: Any) -> Iterator["RunHandle"]:
    """Record a pipeline stage in ops.pipeline_runs; marks FAILED if the body raises."""
    handle = RunHandle(start_run(stage, **kwargs))
    try:
        yield handle
    except Exception as exc:
        finish_run(handle.run_id, "FAILED", handle.rows_in, handle.rows_out, handle.rows_rejected,
                   handle.details, error=f"{type(exc).__name__}: {exc}"[:2000])
        raise
    else:
        finish_run(handle.run_id, "SUCCESS", handle.rows_in, handle.rows_out, handle.rows_rejected, handle.details)


class RunHandle:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.rows_in: int | None = None
        self.rows_out: int | None = None
        self.rows_rejected: int | None = None
        self.details: dict[str, Any] = {}


def record_dq_result(conn, run_id: str | None, layer: str, check_name: str, severity: str, status: str,
                     observed: float | None = None, expected: float | None = None,
                     message: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ops.dq_results (run_id, layer, check_name, severity, status, observed, expected, message) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, layer, check_name, severity, status, observed, expected, message),
        )


# ------------------------------------------------------------------ staging merge
def _columns(cur, table: str) -> list[tuple[str, str, bool, bool]]:
    """(name, formatted type, not_null, has_default) for a table, in column order."""
    schema, name = table.split(".")
    cur.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.atthasdef OR a.attgenerated <> '' "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (schema, name),
    )
    return cur.fetchall()


def merge_from_staging(conn, target: str, staging: str, mode: str, key_cols: Sequence[str] | None = None) -> int:
    """Move rows from a Spark-written staging table into `target`.

    mode:
      replace - TRUNCATE target, then insert (derived analytics tables)
      upsert  - INSERT ... ON CONFLICT DO UPDATE (dimensions)
      sync    - upsert, then delete target rows absent from staging (facts mirror the cleaned data)

    Columns are matched by name; every value is cast to the target column's type.
    Schema drift (extra staging columns, missing required columns) fails loudly.
    Does not commit: the caller owns the transaction, so a multi-table load is atomic.
    """
    if not (_TABLE.match(target) and _TABLE.match(staging)):
        raise ValueError("target/staging must be schema.table identifiers")
    if mode not in {"replace", "upsert", "sync"}:
        raise ValueError(f"unknown mode {mode}")
    if mode != "replace" and not key_cols:
        raise ValueError(f"mode={mode} needs key_cols")

    with conn.cursor() as cur:
        tgt = _columns(cur, target)
        stg = {c[0] for c in _columns(cur, staging)}
        tgt_names = [c[0] for c in tgt]
        extra = sorted(stg - set(tgt_names))
        if extra:
            raise RuntimeError(f"{staging} has columns not in {target}: {extra}")
        missing = [c[0] for c in tgt if c[2] and not c[3] and c[0] not in stg]
        if missing:
            raise RuntimeError(f"{staging} is missing required columns for {target}: {missing}")

        cols = [c for c in tgt if c[0] in stg]
        col_list = ", ".join(f'"{c[0]}"' for c in cols)
        select_list = ", ".join(f'"{c[0]}"::{c[1]}' for c in cols)

        if mode == "replace":
            cur.execute(f"TRUNCATE {target}")
            cur.execute(f"INSERT INTO {target} ({col_list}) SELECT {select_list} FROM {staging}")
            return cur.rowcount

        keys = list(key_cols or [])
        non_key = [c[0] for c in cols if c[0] not in keys]
        sets = [f'"{c}" = EXCLUDED."{c}"' for c in non_key]
        for stamp in ("updated_at", "loaded_at"):
            if stamp in tgt_names and stamp not in stg:
                sets.append(f"{stamp} = now()")
        conflict = ", ".join(f'"{k}"' for k in keys)
        action = f"DO UPDATE SET {', '.join(sets)}" if sets else "DO NOTHING"
        cur.execute(
            f"INSERT INTO {target} ({col_list}) SELECT {select_list} FROM {staging} "
            f"ON CONFLICT ({conflict}) {action}"
        )
        affected = cur.rowcount
        if mode == "sync":
            key_match = " AND ".join(f't."{k}" = s."{k}"::{next(c[1] for c in tgt if c[0] == k)}' for k in keys)
            cur.execute(f"DELETE FROM {target} t WHERE NOT EXISTS (SELECT 1 FROM {staging} s WHERE {key_match})")
        return affected
