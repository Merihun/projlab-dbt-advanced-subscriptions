import os

import pytest


@pytest.fixture
def pg_conninfo() -> dict:
    """Connection settings for the lab database (set by compose.yaml inside the workspace)."""
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "projlab"),
        "password": os.environ.get("POSTGRES_PASSWORD", "projlab"),
        "dbname": os.environ.get("POSTGRES_DB", "projlab"),
        "connect_timeout": 5,
    }
