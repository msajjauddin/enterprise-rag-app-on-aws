"""
Test configuration for the query service. Settings are read at import time, so the
environment is prepared before the app package is imported.
"""
import os
import sys

os.environ.update({
    "STAGE": "test",
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "COGNITO_USER_POOL_ID": "us-east-1_TEST",
    "COGNITO_APP_CLIENT_ID": "ui-client",
    "COGNITO_EVAL_CLIENT_ID": "eval-client",
    "DOCUMENTS_BUCKET": "test-documents",
    "METADATA_TABLE": "test-metadata",
    "CHAT_HISTORY_TABLE": "test-history",
    "RATE_LIMIT_PER_MINUTE": "3",
    "SEMANTIC_CACHE_THRESHOLD": "0.95",
    "RAG_CONFIDENCE_THRESHOLD": "0.35",
    "EVAL_SAMPLE_RATE": "0",
    "MCP_SERVER_URL": "",
})

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import fakeredis  # noqa: E402
import pytest  # noqa: E402

from app import cache  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    server = fakeredis.FakeRedis()
    monkeypatch.setattr(cache, "_client", server)
    monkeypatch.setattr(cache, "client", lambda: server)
    return server
