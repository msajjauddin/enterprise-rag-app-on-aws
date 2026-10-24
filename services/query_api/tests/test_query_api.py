"""End-to-end tests of the query pipeline with Bedrock, Postgres and DynamoDB stubbed."""
import json

import pytest
from fastapi.testclient import TestClient

from app import bedrock, history, main, retrieval, web_search
from app.auth import User, get_current_user
from app.bedrock import GuardrailResult

USER = User(user_id="u1", groups=["finance"], email="jane@example.com")

CHUNKS = [
    {"chunk_id": "c1", "document_id": "d1", "content": "Employees get 25 vacation days.", "metadata": {"page": 2},
     "file_name": "handbook.pdf", "similarity": 0.62, "rrf_score": 0.03},
    {"chunk_id": "c2", "document_id": "d2", "content": "Unused days roll over.", "metadata": {},
     "file_name": "policy.pdf", "similarity": 0.55, "rrf_score": 0.02},
]


@pytest.fixture
def calls():
    return {"search": [], "saved": [], "web": [], "generated": 0}


@pytest.fixture
def client(monkeypatch, calls):
    main.app.dependency_overrides[get_current_user] = lambda: USER

    monkeypatch.setattr(bedrock, "apply_guardrail", lambda text, source: GuardrailResult(False, text))
    monkeypatch.setattr(bedrock, "embed_query", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(bedrock, "rerank", lambda q, docs, n: [(0, 0.91), (1, 0.40)])

    def search(query, embedding, principals, k):
        calls["search"].append(principals)
        return [dict(c) for c in CHUNKS]

    def stream_answer(question, chunks, web_results, usage):
        calls["generated"] += 1
        for token in ["You get ", "25 days ", "[handbook.pdf]."]:
            yield token
        usage.input_tokens, usage.output_tokens, usage.model = 1200, 40, "us.openai.gpt-6-luna"

    monkeypatch.setattr(retrieval, "hybrid_search", search)
    monkeypatch.setattr(bedrock, "stream_answer", stream_answer)
    monkeypatch.setattr(history, "save_turn", lambda *args: calls["saved"].append(args))
    monkeypatch.setattr(web_search, "search", lambda q: calls["web"].append(q) or "Web says 30 days.")

    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def parse_sse(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        event, _, data = block.partition("\ndata: ")
        events.append((event.replace("event: ", ""), json.loads(data)))
    return events


def test_streams_meta_tokens_done(client, calls):
    response = client.post("/query", json={"question": "How many vacation days do I get?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert "".join(d["text"] for e, d in events if e == "token") == "You get 25 days [handbook.pdf]."

    meta = events[0][1]
    assert meta["cached"] is False
    assert [s["file_name"] for s in meta["sources"]] == ["handbook.pdf", "policy.pdf"]
    assert meta["sources"][0]["score"] == 0.91

    # ACL principals from the token are used for retrieval
    assert calls["search"] == [["user:u1", "group:finance"]]
    assert events[-1][1]["usage"] == {"input_tokens": 1200, "output_tokens": 40}
    assert len(calls["saved"]) == 1


def test_second_identical_question_served_from_cache(client, calls):
    client.post("/query", json={"question": "How many vacation days do I get?"})
    response = client.post("/query", json={"question": "How many vacation days do I get?"})
    meta = parse_sse(response.text)[0][1]
    assert meta["cached"] is True
    assert calls["generated"] == 1


def test_weak_retrieval_falls_back_to_web_search(client, calls, monkeypatch):
    monkeypatch.setattr(bedrock, "rerank", lambda q, docs, n: [(0, 0.05)])
    response = client.post("/query", json={"question": "What is the latest tax rule?"})
    meta = parse_sse(response.text)[0][1]
    assert meta["web_search_used"] is True
    assert calls["web"] == ["What is the latest tax rule?"]


def test_web_search_can_be_disabled_per_request(client, calls, monkeypatch):
    monkeypatch.setattr(bedrock, "rerank", lambda q, docs, n: [(0, 0.05)])
    client.post("/query", json={"question": "What is the latest tax rule?", "web_search": False})
    assert calls["web"] == []


def test_input_guardrail_blocks_before_retrieval(client, calls, monkeypatch):
    monkeypatch.setattr(bedrock, "apply_guardrail",
                        lambda text, source: GuardrailResult(source == "INPUT", "Sorry, I can't help with that request."))
    events = parse_sse(client.post("/query", json={"question": "something harmful"}).text)
    assert events[1] == ("token", {"text": "Sorry, I can't help with that request."})
    assert events[-1][1]["guardrail_intervened"] is True
    assert calls["search"] == [] and calls["generated"] == 0


def test_output_guardrail_replaces_answer(client, monkeypatch):
    monkeypatch.setattr(bedrock, "apply_guardrail",
                        lambda text, source: GuardrailResult(source == "OUTPUT", "Sorry, I can't share that answer."))
    events = parse_sse(client.post("/query", json={"question": "How many vacation days do I get?"}).text)
    assert ("replace", {"text": "Sorry, I can't share that answer."}) in events


def test_non_streaming_returns_json(client):
    response = client.post("/query", json={"question": "How many vacation days do I get?", "stream": False})
    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "You get 25 days [handbook.pdf]."
    assert body["message_id"] and body["sources"]


def test_rate_limited_user_gets_429(client):
    # RATE_LIMIT_PER_MINUTE=3 in conftest
    statuses = [client.post("/query", json={"question": f"q{i}"}).status_code for i in range(4)]
    assert statuses == [200, 200, 200, 429]


def test_generation_error_is_reported_as_error_event(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("bedrock down")
        yield  # pragma: no cover

    monkeypatch.setattr(bedrock, "stream_answer", broken)
    events = parse_sse(client.post("/query", json={"question": "anything"}).text)
    assert events[-1][0] == "error"


def test_upload_url_rejects_groups_user_is_not_in(client):
    response = client.post("/documents/upload-url",
                           json={"file_name": "plan.pdf", "share_with_groups": ["executives"]})
    assert response.status_code == 403


def test_upload_url_rejects_unsupported_types(client):
    response = client.post("/documents/upload-url", json={"file_name": "run.exe"})
    assert response.status_code == 400


def test_upload_url_returns_presigned_post(client, monkeypatch):
    stored = []
    monkeypatch.setattr(main, "metadata_table", lambda: type("T", (), {"put_item": lambda self, Item: stored.append(Item)})())
    response = client.post("/documents/upload-url",
                           json={"file_name": "Q3 plan.pdf", "content_type": "application/pdf",
                                 "share_with_groups": ["finance"]})
    assert response.status_code == 200
    body = response.json()
    fields = body["upload"]["fields"]
    assert fields["key"] == f"uploads/u1/{body['document_id']}/Q3 plan.pdf"
    assert fields["x-amz-meta-acl"] == "group:finance"
    assert stored[0]["status"] == "uploading"


def test_health_needs_no_auth():
    main.app.dependency_overrides.clear()
    assert TestClient(main.app).get("/health").json()["status"] == "ok"
