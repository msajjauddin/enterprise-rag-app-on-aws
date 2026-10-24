"""
PostgreSQL connection pool for retrieval. The query service only reads, so it uses
the reader endpoint (RDS Proxy read-only endpoint in prod); ingestion writes.
"""
import json
import logging
from typing import Optional

import boto3
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from .config import settings

logger = logging.getLogger(__name__)

_read_pool: Optional[ConnectionPool] = None
_credentials: Optional[dict] = None


def _get_credentials() -> dict:
    global _credentials
    if _credentials is None:
        secret = boto3.client("secretsmanager", region_name=settings.aws_region).get_secret_value(
            SecretId=settings.db_secret_arn)
        _credentials = json.loads(secret["SecretString"])
    return _credentials


def _conninfo(host: str) -> str:
    creds = _get_credentials()
    return make_conninfo(
        host=host,
        port=5432,
        dbname=settings.db_name,
        user=creds["username"],
        password=creds["password"],
        sslmode="require",
        # Non-prod Serverless v2 may be resuming from auto-pause (~15 s)
        connect_timeout=30,
        application_name=f"query-api-{settings.stage}",
    )


def read_pool() -> ConnectionPool:
    global _read_pool
    if _read_pool is None:
        _read_pool = ConnectionPool(
            conninfo=_conninfo(settings.db_read_host),
            min_size=1,
            max_size=settings.db_pool_max,
            max_idle=300,
            timeout=35,
            open=True,
            wait=False,  # don't block startup or health checks on the database
        )
    return _read_pool


def close_pools() -> None:
    global _read_pool
    if _read_pool is not None:
        _read_pool.close()
        _read_pool = None
