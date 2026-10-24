"""Test cases for the db_init Lambda function."""
import json
import os
import socket
import unittest
from unittest.mock import MagicMock, patch

"""Set up test environment."""
# Set environment variables
os.environ["DB_HOST"] = "cluster.test"
os.environ["DB_NAME"] = "ragapp"
os.environ["DB_MASTER_SECRET_ARN"] = "master-secret"
os.environ["DB_APP_SECRET_ARN"] = "app-secret"
os.environ["STAGE"] = "test"
os.environ["EMBEDDING_DIMENSIONS"] = "1024"
os.environ["MAX_RETRIES"] = "2"
os.environ["RETRY_DELAY"] = "0"  # No delay for tests

# Now import the module under test - mocks are already in place globally from conftest
from db_init import db_init
from db_init.db_init import handler, get_secret, check_dns_resolution, create_schema, connect_with_retry

SECRETS = {
    "master-secret": {"username": "ragadmin", "password": "master-pw"},
    "app-secret": {"username": "rag_app", "password": "app-pw"},
}


class TestDbInit(unittest.TestCase):
    """Test cases for the db_init Lambda function."""

    @patch("db_init.db_init.secretsmanager")
    def test_get_secret(self, mock_secretsmanager):
        mock_secretsmanager.get_secret_value.return_value = {"SecretString": json.dumps(SECRETS["app-secret"])}
        self.assertEqual(get_secret("app-secret"), SECRETS["app-secret"])
        mock_secretsmanager.get_secret_value.assert_called_once_with(SecretId="app-secret")

    @patch("socket.gethostbyname")
    def test_check_dns_resolution(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "10.0.4.10"
        self.assertTrue(check_dns_resolution("cluster.test"))
        mock_gethostbyname.side_effect = socket.gaierror("nope")
        self.assertFalse(check_dns_resolution("cluster.test"))

    @patch("db_init.db_init.check_dns_resolution", return_value=False)
    @patch("db_init.db_init.time.sleep")
    def test_connect_gives_up_when_dns_never_resolves(self, mock_sleep, mock_dns):
        with self.assertRaises(RuntimeError):
            connect_with_retry(SECRETS["master-secret"])
        self.assertEqual(mock_dns.call_count, 3)  # first try + MAX_RETRIES

    def test_create_schema_creates_hnsw_and_acl(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1024,)
        create_schema(cursor, 1024)

        statements = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS document_acl", statements)
        self.assertIn("USING hnsw (embedding vector_cosine_ops)", statements)
        self.assertIn("USING gin (content_tsv)", statements)
        self.assertNotIn("ivfflat", statements)

    def test_create_schema_rejects_dimension_change(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (768,)
        with self.assertRaises(RuntimeError):
            create_schema(cursor, 1024)

    @patch("db_init.db_init.connect_with_retry")
    @patch("db_init.db_init.get_secret", side_effect=lambda arn: SECRETS[arn])
    def test_handler_success(self, mock_get_secret, mock_connect):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.side_effect = [(1024,), None]  # embedding dims, then role does not exist yet
        mock_connect.return_value = conn

        response = handler({}, None)

        self.assertEqual(response["statusCode"], 200)
        mock_connect.assert_called_once_with(SECRETS["master-secret"])
        conn.close.assert_called_once()

    @patch("db_init.db_init.connect_with_retry", side_effect=RuntimeError("db down"))
    @patch("db_init.db_init.get_secret", side_effect=lambda arn: SECRETS[arn])
    def test_handler_raises_so_ci_fails(self, mock_get_secret, mock_connect):
        with self.assertRaises(RuntimeError):
            handler({}, None)

    def test_healthcheck(self):
        self.assertEqual(handler({"action": "healthcheck"}, None)["statusCode"], 200)


if __name__ == "__main__":
    unittest.main()
