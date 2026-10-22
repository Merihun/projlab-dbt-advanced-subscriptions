"""Verify your lab for this project. Run inside the workspace: python -m pytest -q tests/test_environment.py"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def dbt(*args: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DBT_TARGET_PATH": str(tmp_path / "target"), "DBT_LOG_PATH": str(tmp_path / "logs"),
           "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}  # fmt: skip
    command = [sys.executable, "-m", "dbt.cli.main", *args, "--project-dir", str(ROOT), "--profiles-dir", str(ROOT)]
    return subprocess.run(command, capture_output=True, text=True, timeout=300, env=env, check=False)


def test_python_version():
    assert sys.version_info[:2] == (3, 12), sys.version


def test_packages_import():
    import dbt.adapters.duckdb  # noqa: F401
    import dbt.adapters.postgres  # noqa: F401
    import duckdb  # noqa: F401
    import psycopg  # noqa: F401
    import sqlfluff  # noqa: F401


def test_practice_data_loads_into_duckdb(tmp_path):
    from fernhill_subs import data
    from fernhill_subs.load import load

    data.generate(tmp_path)
    counts = load(1, "ci", tmp_path / "data", str(tmp_path / "check.duckdb"))
    assert counts["plan_prices"] == 15 and counts["customers"] > 1000, counts


@pytest.mark.service
def test_postgres_is_reachable(pg_conninfo):
    import psycopg

    with psycopg.connect(**pg_conninfo) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
    assert version.startswith("16."), version


@pytest.mark.service
def test_dbt_connects_to_the_warehouse(tmp_path):
    result = dbt("debug", "--target", "dev", tmp_path=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
