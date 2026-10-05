"""PostgreSQL behaviour: migrations, constraints, staging merges, role permissions.

Needs the postgres service (docker compose up -d postgres); skipped otherwise.
"""
from __future__ import annotations

import os

import psycopg2
import pytest

from profitpulse.db import connect, merge_from_staging, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture()
def conn(db):
    c = connect()
    yield c
    c.rollback()
    c.close()


def test_migrations_are_idempotent_and_tracked(db):
    assert run_migrations() == []                          # nothing pending on a migrated database
    with db.cursor() as cur:
        cur.execute("SELECT version FROM ops.schema_migrations ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    assert versions[:2] == ["001_core_star_schema.sql", "002_quarantine_and_ops.sql"]
    assert versions == sorted(versions)


def test_editing_an_applied_migration_is_refused(db, tmp_path, monkeypatch):
    from profitpulse import settings as st
    first = sorted(st.get_settings().migrations_dir.glob("*.sql"))[0]
    (tmp_path / first.name).write_text(first.read_text() + "\n-- tampered\n")
    monkeypatch.setenv("PP_MIGRATIONS_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="modified after being applied"):
        run_migrations()


# ------------------------------------------------------------------ constraints
def _insert_sale(cur, **over):
    row = dict(event_id="EVT9999999", event_ts="2025-01-01 10:00:00", date_key=20250101, branch_id="BR01",
               product_id="P0001", quantity=2, unit_price=10, unit_cost=5, discount_pct=0.1, gross_sales=20,
               discount_amount=2, net_revenue=18, cogs=10, gross_profit=8)
    row.update(over)
    cols = ", ".join(row)
    cur.execute(f"INSERT INTO core.fact_sales ({cols}) VALUES ({', '.join(['%s'] * len(row))})", list(row.values()))


@pytest.mark.parametrize("override,why", [
    ({"quantity": 0}, "positive quantity"),
    ({"discount_pct": 1.2}, "discount below 100%"),
    ({"net_revenue": 0}, "positive revenue"),
    ({"branch_id": "BR99"}, "branch foreign key"),
    ({"product_id": "P9999"}, "product foreign key"),
    ({"date_key": 19000101}, "date foreign key"),
])
def test_fact_constraints_reject_bad_rows(conn, override, why):
    with conn.cursor() as cur, pytest.raises(psycopg2.errors.IntegrityError):
        _insert_sale(cur, **override)


def test_primary_key_blocks_duplicate_events(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, event_ts, date_key, branch_id, product_id FROM core.fact_sales LIMIT 1")
        existing = cur.fetchone()
        if not existing:
            pytest.skip("warehouse is empty")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            _insert_sale(cur, event_id=existing[0], event_ts=existing[1], date_key=existing[2],
                         branch_id=existing[3], product_id=existing[4])


def test_reader_role_is_read_only(db):
    password = os.environ.get("PP_READER_PASSWORD")
    if not password:
        pytest.skip("reader credentials not configured")
    reader = connect(user=os.environ.get("PP_READER_USER", "pp_reader"), password=password)
    try:
        with reader.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM analytics.kpi_summary")           # reads work
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM analytics.kpi_summary")
            reader.rollback()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("SELECT 1 FROM staging.dq_failures LIMIT 1")        # staging is not exposed
    finally:
        reader.close()


# ------------------------------------------------------------------ staging merges
@pytest.fixture()
def tmp_tables(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS staging.t_src, staging.t_tgt")
        cur.execute("CREATE TABLE staging.t_src (id text, qty double precision, payload text)")
        cur.execute("CREATE TABLE staging.t_tgt (id varchar(10) PRIMARY KEY, qty integer NOT NULL, "
                    "payload jsonb, updated_at timestamptz NOT NULL DEFAULT now())")
    yield
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS staging.t_src, staging.t_tgt")
    conn.commit()


def _fill(conn, rows):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE staging.t_src")
        for r in rows:
            cur.execute("INSERT INTO staging.t_src VALUES (%s,%s,%s)", r)


def _tgt(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, qty, payload FROM staging.t_tgt ORDER BY id")
        return cur.fetchall()


def test_upsert_casts_types_and_updates_in_place(conn, tmp_tables):
    _fill(conn, [("a", 1.0, '{"k": 1}'), ("b", 2.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "upsert", ["id"])
    assert _tgt(conn) == [("a", 1, {"k": 1}), ("b", 2, None)]                 # text -> jsonb, double -> integer
    _fill(conn, [("a", 9.0, '{"k": 2}'), ("c", 3.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "upsert", ["id"])
    assert _tgt(conn) == [("a", 9, {"k": 2}), ("b", 2, None), ("c", 3, None)]   # upsert never deletes


def test_sync_mirrors_the_source_including_deletes(conn, tmp_tables):
    _fill(conn, [("a", 1.0, None), ("b", 2.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "sync", ["id"])
    _fill(conn, [("b", 5.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "sync", ["id"])
    assert _tgt(conn) == [("b", 5, None)]


def test_replace_truncates_first(conn, tmp_tables):
    _fill(conn, [("a", 1.0, None), ("b", 2.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "replace")
    _fill(conn, [("z", 7.0, None)])
    merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "replace")
    assert _tgt(conn) == [("z", 7, None)]


def test_schema_drift_fails_loudly(conn, tmp_tables):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE staging.t_src ADD COLUMN surprise text")
    with pytest.raises(RuntimeError, match="not in"):
        merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "upsert", ["id"])
    conn.rollback()


def test_missing_required_column_fails_loudly(conn, tmp_tables):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE staging.t_src DROP COLUMN qty")
    with pytest.raises(RuntimeError, match="missing required"):
        merge_from_staging(conn, "staging.t_tgt", "staging.t_src", "upsert", ["id"])
    conn.rollback()


def test_unsafe_table_names_are_refused(conn):
    with pytest.raises(ValueError):
        merge_from_staging(conn, "core.fact_sales; DROP TABLE x", "staging.t_src", "replace")
