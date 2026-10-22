"""The loader: what an ELT tool would do with the billing system's and the app's exports.

Batch 1 creates the schema ``raw`` from nothing (dropping it first, with any views that read it). Batch 2 must
come after batch 1: it replaces the three full exports (customers, plan_prices, subscriptions), appends the rest,
and adds the columns that are new in batch 2 (``invoice_lines.amount_minor``, ``app_events.app_version``) the way
a loader does when a source changes shape: old rows keep NULL there. Every row gets ``_loaded_at``, the time it
was loaded (UTC).

Targets: ``ci`` loads the DuckDB file (``DBT_DUCKDB_PATH``, default ``fernhill.duckdb``); ``dev`` and ``prod`` load
the lab's PostgreSQL (``POSTGRES_HOST`` and friends), where both read the same ``raw`` schema.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fernhill_subs.data import REPLACED, TABLES

TYPES = {
    "customer_id": "varchar", "email": "varchar", "country_code": "varchar", "currency": "varchar",
    "signed_up_at": "timestamp", "updated_at": "timestamp", "price_id": "varchar", "plan_id": "varchar",
    "plan_name": "varchar", "billing_interval": "varchar", "unit_amount": "integer", "active": "boolean",
    "subscription_id": "varchar", "quantity": "integer", "status": "varchar", "created_at": "timestamp",
    "event_id": "varchar", "event_type": "varchar", "occurred_at": "timestamp", "received_at": "timestamp",
    "line_id": "varchar", "invoice_id": "varchar", "line_type": "varchar", "amount_cents": "bigint",
    "amount_minor": "bigint", "period_start": "date", "period_end": "date", "issued_at": "timestamp",
    "event_name": "varchar", "app_version": "varchar",
}  # fmt: skip


def duckdb_path() -> str:
    return os.environ.get("DBT_DUCKDB_PATH", "fernhill.duckdb")


def postgres_settings() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "projlab"),
        "password": os.environ.get("POSTGRES_PASSWORD", "projlab"),
        "dbname": os.environ.get("POSTGRES_DB", "projlab"),
    }


def _create(table: str) -> str:
    columns = ", ".join(f"{c} {TYPES[c]}" for c in TABLES[table][0])
    return f"create table raw.{table} ({columns}, _loaded_at timestamp)"


def _new_columns(table: str) -> list[str]:
    return [c for c in TABLES[table][1] if c not in TABLES[table][0]]


def load(batch: int, target: str = "dev", data_dir: str | Path = "data", database: str | None = None) -> dict[str, int]:
    """Load data/batch_<batch>/*.csv into the schema raw of the target's warehouse; returns rows per table."""
    folder = Path(data_dir) / f"batch_{batch}"
    if not (folder / "customers.csv").is_file():
        raise SystemExit(f"{folder}/ is missing: run `python -m fernhill_subs generate` (make data) first")
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    if target == "ci":
        return _load_duckdb(batch, folder, database or duckdb_path(), loaded_at)
    return _load_postgres(batch, folder, loaded_at)


def _batches_loaded(rows) -> set[int]:
    return {r[0] for r in rows}


def _load_duckdb(batch: int, folder: Path, path: str, loaded_at: datetime) -> dict[str, int]:
    import duckdb

    con = duckdb.connect(path)
    try:
        _prepare(batch, lambda sql: con.execute(sql).fetchall())
        counts = {}
        for table in TABLES:
            columns = TABLES[table][batch - 1]
            if table in REPLACED:
                con.execute(f"delete from raw.{table}")
            spec = "{" + ", ".join(f"'{c}': '{TYPES[c]}'" for c in columns) + "}"
            file = (folder / f"{table}.csv").as_posix()
            con.execute(
                f"insert into raw.{table} ({', '.join(columns)}, _loaded_at) "
                f"select {', '.join(columns)}, ?::timestamp from read_csv(?, header = true, columns = {spec})",
                [loaded_at, file],
            )
            counts[table] = con.execute(f"select count(*) from raw.{table}").fetchone()[0]
        con.execute("insert into raw._batches values (?, ?)", [batch, loaded_at])
        return counts
    finally:
        con.close()


def _load_postgres(batch: int, folder: Path, loaded_at: datetime) -> dict[str, int]:
    import psycopg

    with psycopg.connect(**postgres_settings(), autocommit=False) as conn:

        def run(sql: str) -> list:
            cursor = conn.execute(sql)
            return cursor.fetchall() if cursor.description else []

        _prepare(batch, run)
        counts = {}
        for table in TABLES:
            columns = TABLES[table][batch - 1]
            if table in REPLACED:
                conn.execute(f"truncate raw.{table}")
            with conn.cursor() as cur:
                with cur.copy(f"copy raw.{table} ({', '.join(columns)}) from stdin with (format csv, header true)") as copy:
                    copy.write((folder / f"{table}.csv").read_bytes())
                cur.execute(f"update raw.{table} set _loaded_at = %s where _loaded_at is null", [loaded_at])
            counts[table] = conn.execute(f"select count(*) from raw.{table}").fetchone()[0]
        conn.execute("insert into raw._batches values (%s, %s)", [batch, loaded_at])
        conn.commit()
        return counts


def _prepare(batch: int, run) -> None:
    """Batch 1: a fresh raw schema. Batch 2: check batch 1 is in and batch 2 is not, then add the new columns."""
    if batch == 1:
        run("drop schema if exists raw cascade")
        run("create schema raw")
        run("create table raw._batches (batch integer, loaded_at timestamp)")
        for table in TABLES:
            run(_create(table))
        return
    loaded = _batches_loaded(run("select batch from raw._batches")) if _has_raw(run) else set()
    if 1 not in loaded:
        raise SystemExit("load batch 1 first: python -m fernhill_subs load --batch 1")
    if batch in loaded:
        raise SystemExit(f"batch {batch} is already loaded; load batch 1 again to start over")
    for table in TABLES:
        for column in _new_columns(table):
            run(f"alter table raw.{table} add column {column} {TYPES[column]}")


def _has_raw(run) -> bool:
    rows = run("select count(*) from information_schema.tables where table_schema = 'raw' and table_name = '_batches'")
    return bool(rows and rows[0][0])
