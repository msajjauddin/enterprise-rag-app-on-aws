"""Tests for the GPT-on-Bedrock generation and judge wrappers (OpenAI SDK stubbed)."""
import json
from types import SimpleNamespace

import pytest

from app import bedrock
from app.config import settings


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_response(self):
        return self.final


class FakeResponses:
    def __init__(self, stream=None, response=None):
        self._stream, self._response, self.calls = stream, response, []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._stream

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


@pytest.fixture
def fake_llm(monkeypatch):
    def install(**kwargs):
        responses = FakeResponses(**kwargs)
        monkeypatch.setattr(bedrock, "llm", lambda: SimpleNamespace(responses=responses))
        return responses
    return install


def delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def test_stream_answer_yields_text_and_records_usage(fake_llm):
    final = SimpleNamespace(usage=SimpleNamespace(input_tokens=900, output_tokens=30),
                            model="us.openai.gpt-6-luna", status="completed", incomplete_details=None)
    events = [SimpleNamespace(type="response.created"), delta("You get "), delta("24 days."),
              SimpleNamespace(type="response.completed")]
    responses = fake_llm(stream=FakeStream(events, final))

    usage = bedrock.GenerationUsage()
    chunks = [{"file_name": "leave.md", "content": "24 days of annual leave."}]
    text = "".join(bedrock.stream_answer("How much leave?", chunks, None, usage))

    assert text == "You get 24 days."
    assert (usage.input_tokens, usage.output_tokens, usage.stop_reason) == (900, 30, "completed")
    call = responses.calls[0]
    assert call["model"] == settings.generation_model_id
    assert call["instructions"] == bedrock.SYSTEM_PROMPT
    assert call["reasoning"] == {"effort": settings.generation_effort}
    assert '<document index="1" source="leave.md">' in call["input"]
    assert call["store"] is False


def test_stream_answer_reports_truncation(fake_llm):
    final = SimpleNamespace(usage=None, model="m", status="incomplete",
                            incomplete_details=SimpleNamespace(reason="max_output_tokens"))
    fake_llm(stream=FakeStream([delta("partial")], final))
    usage = bedrock.GenerationUsage()
    list(bedrock.stream_answer("q", [], None, usage))
    assert usage.stop_reason == "max_output_tokens"


def test_judge_uses_strict_json_schema(fake_llm):
    response = SimpleNamespace(status="completed", output_text=json.dumps({"score": 0.8}))
    responses = fake_llm(response=response)
    schema = {"type": "object", "properties": {"score": {"type": "number"}},
              "required": ["score"], "additionalProperties": False}

    assert bedrock.judge("grade this", schema) == {"score": 0.8}
    fmt = responses.calls[0]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"] == schema
    assert responses.calls[0]["model"] == settings.judge_model_id


def test_judge_raises_when_incomplete(fake_llm):
    fake_llm(response=SimpleNamespace(status="incomplete", output_text=""))
    with pytest.raises(RuntimeError):
        bedrock.judge("grade this", {})
