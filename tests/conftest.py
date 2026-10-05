from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/helpers.py


@pytest.fixture(scope="session")
def spark():
    """One local SparkSession for the whole run (JVM start-up is the slow part)."""
    from profitpulse.spark.session import get_spark
    session = get_spark("profitpulse-tests", spark__sql__shuffle__partitions="4")
    yield session
    session.stop()


@pytest.fixture(scope="session")
def rules_cfg():
    from profitpulse.config import rules_config
    return rules_config()


@pytest.fixture(scope="session")
def analytics_cfg():
    from profitpulse.config import analytics_config
    return analytics_config()


def _db_available() -> bool:
    try:
        from profitpulse.db import connect
        connect().close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db():
    """Skips integration tests when PostgreSQL is not reachable with the configured credentials."""
    if not os.environ.get("PP_APP_PASSWORD") or not _db_available():
        pytest.skip("PostgreSQL not available")
    from profitpulse.db import connect
    conn = connect()
    yield conn
    conn.close()
