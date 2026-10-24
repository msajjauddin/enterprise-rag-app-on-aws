"""Test cases for the document_processor Lambda function."""
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

"""Set up test environment."""
# Set environment variables
os.environ["DOCUMENTS_BUCKET"] = "test-bucket"
os.environ["METADATA_TABLE"] = "test-table"
os.environ["STAGE"] = "test"
os.environ["DB_HOST"] = "proxy.test"
os.environ["DB_NAME"] = "ragapp"
os.environ["DB_SECRET_ARN"] = "test-db-secret"
os.environ["EMBEDDING_MODEL_ID"] = "amazon.titan-embed-text-v2:0"
os.environ["EMBEDDING_DIMENSIONS"] = "4"
os.environ["EMBEDDING_BATCH_SIZE"] = "2"

# Now import the module under test - mocks are already in place globally from conftest
from document_processor import document_processor as dp
from document_processor.document_processor import (
    handler, jobs_from_message, parse_acl, embed_documents, get_mime_type, to_vector_literal
)


def s3_event(key, bucket="test-bucket"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def sqs_event(*bodies):
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(b)} for i, b in enumerate(bodies)]}


def invoke_response(payload):
    return {"body": io.BytesIO(json.dumps(payload).encode())}


class TestMessageParsing(unittest.TestCase):

    def test_s3_event_key_is_decoded_and_split(self):
        jobs = jobs_from_message(s3_event("uploads/user-1/doc-1/Travel+Policy%282026%29.pdf"))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["key"], "uploads/user-1/doc-1/Travel Policy(2026).pdf")
        self.assertEqual(job["owner_id"], "user-1")
        self.assertEqual(job["document_id"], "doc-1")
        self.assertIsNone(job["acl"])  # read from S3 object metadata later

    def test_s3_test_event_is_ignored(self):
        self.assertEqual(jobs_from_message({"Event": "s3:TestEvent"}), [])

    def test_unexpected_key_format_is_skipped(self):
        self.assertEqual(jobs_from_message(s3_event("uploads/orphan.pdf")), [])

    def test_connector_message(self):
        jobs = jobs_from_message({
            "bucket": "connector-bucket",
            "key": "sharepoint/hr/handbook.pdf",
            "owner_id": "sharepoint-sync",
            "acl": ["group:hr", "group:all-staff"],
            "source": "sharepoint",
        })
        self.assertEqual(jobs[0]["acl"], "group:hr,group:all-staff")
        self.assertEqual(jobs[0]["source"], "sharepoint")
        # Stable id so re-syncs replace the same document
        self.assertEqual(jobs[0]["document_id"],
                         jobs_from_message({"key": "sharepoint/hr/handbook.pdf"})[0]["document_id"])

    def test_unknown_message_raises(self):
        with self.assertRaises(ValueError):
            jobs_from_message({"hello": "world"})


class TestHelpers(unittest.TestCase):

    def test_acl_always_includes_owner_and_drops_junk(self):
        self.assertEqual(parse_acl("u1", "group:hr, junk, user:u2,group:hr"),
                         ["group:hr", "user:u1", "user:u2"])
        self.assertEqual(parse_acl("u1", None), ["user:u1"])

    def test_mime_types(self):
        self.assertEqual(get_mime_type("a.PDF"), "application/pdf")
        self.assertEqual(get_mime_type("notes.md"), "text/markdown")
        self.assertEqual(get_mime_type("x.bin"), "application/octet-stream")

    def test_vector_literal(self):
        self.assertEqual(to_vector_literal([0.1, -2.0]), "[0.1,-2.0]")


class TestEmbeddings(unittest.TestCase):

    @patch("document_processor.document_processor.bedrock_runtime")
    def test_titan_embeds_each_text(self, mock_bedrock):
        mock_bedrock.invoke_model.side_effect = lambda **kw: invoke_response(
            {"embedding": [float(len(json.loads(kw["body"])["inputText"]))] * 4})
        result = embed_documents(["a", "bb", "ccc"])
        self.assertEqual([r[0] for r in result], [1.0, 2.0, 3.0])
        body = json.loads(mock_bedrock.invoke_model.call_args.kwargs["body"])
        self.assertEqual(body["dimensions"], 4)
        self.assertTrue(body["normalize"])

    @patch("document_processor.document_processor.bedrock_runtime")
    def test_cohere_embeds_in_batches(self, mock_bedrock):
        mock_bedrock.invoke_model.side_effect = lambda **kw: invoke_response(
            {"embeddings": {"float": [[1.0] * 4 for _ in json.loads(kw["body"])["texts"]]}})
        with patch.object(dp, "EMBEDDING_MODEL_ID", "cohere.embed-v4:0"):
            result = embed_documents(["a", "b", "c"])
        self.assertEqual(len(result), 3)
        self.assertEqual(mock_bedrock.invoke_model.call_count, 2)  # batch size 2

    @patch("document_processor.document_processor.bedrock_runtime")
    def test_embedding_errors_propagate(self, mock_bedrock):
        mock_bedrock.invoke_model.side_effect = RuntimeError("throttled")
        with self.assertRaises(RuntimeError):
            embed_documents(["a"])


class TestHandler(unittest.TestCase):

    def test_healthcheck(self):
        response = handler({"action": "healthcheck"}, None)
        self.assertEqual(response["statusCode"], 200)

    @patch("document_processor.document_processor.process_document")
    def test_reports_only_failed_messages(self, mock_process):
        mock_process.side_effect = [3, RuntimeError("boom")]
        result = handler(sqs_event(s3_event("uploads/u1/d1/a.pdf"), s3_event("uploads/u1/d2/b.pdf")), None)
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "m1"}]})

    @patch("document_processor.document_processor.process_document")
    def test_malformed_body_is_a_failure(self, mock_process):
        event = {"Records": [{"messageId": "bad", "body": "not json"}]}
        self.assertEqual(handler(event, None), {"batchItemFailures": [{"itemIdentifier": "bad"}]})
        mock_process.assert_not_called()


class TestProcessDocument(unittest.TestCase):

    def setUp(self):
        patchers = {
            "s3": patch("document_processor.document_processor.s3_client"),
            "dynamodb": patch("document_processor.document_processor.dynamodb"),
            "embed": patch("document_processor.document_processor.embed_documents"),
            "loader": patch("document_processor.document_processor.get_document_loader"),
            "chunk": patch("document_processor.document_processor.chunk_documents"),
            "connect": patch("document_processor.document_processor.get_postgres_connection"),
            "creds": patch("document_processor.document_processor.get_postgres_credentials"),
            "execute_values": patch("document_processor.document_processor.execute_values"),
        }
        self.mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)

        self.mocks["s3"].head_object.return_value = {"Metadata": {"acl": "group:finance"}}
        chunk = MagicMock(page_content="Employees get 25 vacation days.", metadata={"page": 1})
        self.mocks["chunk"].return_value = [chunk, chunk]
        self.mocks["embed"].return_value = [[0.1] * 4, [0.2] * 4]

        self.cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = self.cursor
        self.mocks["connect"].return_value = conn
        self.conn = conn

    def job(self, **overrides):
        job = {"bucket": "test-bucket", "key": "uploads/u1/d1/handbook.pdf", "owner_id": "u1",
               "document_id": "d1", "file_name": "handbook.pdf", "acl": None, "source": "s3"}
        job.update(overrides)
        return job

    def test_replaces_chunks_and_acl_in_one_transaction(self):
        self.assertEqual(dp.process_document(self.job()), 2)

        statements = " ".join(c.args[0] for c in self.cursor.execute.call_args_list)
        self.assertIn("ON CONFLICT (document_id) DO UPDATE", statements)
        self.assertIn("DELETE FROM chunks", statements)
        self.assertIn("DELETE FROM document_acl", statements)

        acl_call, chunk_call = self.mocks["execute_values"].call_args_list
        self.assertEqual(acl_call.args[2], [("d1", "group:finance"), ("d1", "user:u1")])
        rows = chunk_call.args[2]
        self.assertEqual([r[2] for r in rows], [0, 1])
        self.assertEqual(rows[0][5], "[0.1,0.1,0.1,0.1]")
        self.conn.close.assert_called_once()

        item = self.mocks["dynamodb"].Table.return_value.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["status"], "processed")
        self.assertEqual(item["num_chunks"], 2)

    def test_connector_acl_overrides_object_metadata(self):
        dp.process_document(self.job(acl="group:hr", owner_id="sharepoint-sync"))
        acl_rows = self.mocks["execute_values"].call_args_list[0].args[2]
        self.assertEqual(acl_rows, [("d1", "group:hr"), ("d1", "user:sharepoint-sync")])

    def test_embedding_failure_writes_nothing(self):
        self.mocks["embed"].side_effect = RuntimeError("throttled")
        with self.assertRaises(RuntimeError):
            dp.process_document(self.job())
        self.mocks["connect"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
