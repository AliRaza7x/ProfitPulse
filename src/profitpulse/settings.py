"""Runtime settings, read from environment variables.

Inside Docker the compose file provides everything. On the host, defaults point
at the published ports (127.0.0.1:15432 / :19092), so the same code works in both.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str
    reader_role: str
    kafka_bootstrap: str
    topic_prefix: str
    spark_master: str
    spark_driver_memory: str
    lake_dir: Path
    config_dir: Path
    data_dir: Path
    migrations_dir: Path
    extra_jars_dir: Path
    currency: str

    @property
    def jdbc_url(self) -> str:
        return f"jdbc:postgresql://{self.pg_host}:{self.pg_port}/{self.pg_db}"

    def topic(self, short_name: str) -> str:
        return f"{self.topic_prefix}.{short_name}"


def get_settings() -> Settings:
    lake = Path(os.environ.get("PP_LAKE_DIR", REPO_ROOT / "data" / "lake"))
    return Settings(
        pg_host=_env("POSTGRES_HOST", "localhost"),
        pg_port=int(_env("POSTGRES_PORT", os.environ.get("PP_POSTGRES_HOST_PORT", "15432"))),
        pg_db=_env("POSTGRES_DB", "profitpulse"),
        pg_user=_env("PP_APP_USER", "pp_app"),
        pg_password=_env("PP_APP_PASSWORD", ""),
        reader_role=_env("PP_READER_USER", "pp_reader"),
        kafka_bootstrap=_env(
            "KAFKA_BOOTSTRAP_SERVERS", f"localhost:{os.environ.get('PP_KAFKA_HOST_PORT', '19092')}"
        ),
        topic_prefix=_env("KAFKA_TOPIC_PREFIX", "pp"),
        spark_master=_env("SPARK_MASTER", "local[4]"),
        spark_driver_memory=_env("SPARK_DRIVER_MEMORY", "3g"),
        lake_dir=lake,
        config_dir=Path(os.environ.get("PP_CONFIG_DIR", REPO_ROOT / "config")),
        data_dir=Path(os.environ.get("PP_DATA_DIR", REPO_ROOT / "data")),
        migrations_dir=Path(os.environ.get("PP_MIGRATIONS_DIR", REPO_ROOT / "database" / "migrations")),
        extra_jars_dir=Path(os.environ.get("PP_EXTRA_JARS_DIR", "/opt/spark-extra-jars")),
        currency=_env("PP_CURRENCY", "PKR"),
    )
