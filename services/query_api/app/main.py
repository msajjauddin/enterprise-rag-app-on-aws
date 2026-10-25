"""
FastAPI query service for the RAG application.

POST /query streams the answer as Server-Sent Events:
  meta    {message_id, conversation_id, cached, web_search_used, sources}
  token   {text}                       answer text delta
  replace {text}                       guardrail replaced the answer so far
  done    {usage, latency_ms, ...}
  error   {message}
With "stream": false the same pipeline returns one JSON document (used by CI evals).
"""
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Iterator, List, Optional

import anyio
import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import bedrock, cache, evaluation, history, retrieval, web_search
from .auth import User, get_current_user
from .config import settings
from .db import close_pools
from .telemetry import emit_metrics, setup_tracing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("query_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Endpoints are sync (boto3, psycopg); size the worker thread pool for ~30 in-flight streams per task
    anyio.to_thread.current_default_thread_limiter().total_tokens = 100
    yield
    close_pools()


app = FastAPI(title="RAG Query API", version="2.0.0", lifespan=lifespan)
setup_tracing(app)

_s3 = None
_metadata_table = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=settings.aws_region, config=Config(signature_version="s3v4"))
    return _s3


def metadata_table():
    global _metadata_table
    if _metadata_table is None:
        _metadata_table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.metadata_table)
    return _metadata_table


# =========================
# Models
# =========================

class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    web_search: bool = True
    stream: bool = True


class FeedbackRequest(BaseModel):
    message_id: str = Field(max_length=64)
    rating: int = Field(ge=-1, le=1)
    comment: Optional[str] = Field(default=None, max_length=2000)


class UploadRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=127)
    share_with_groups: List[str] = Field(default_factory=list, max_length=50)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def source_summary(chunks: List[dict]) -> List[dict]:
    return [
        {
            "file_name": c["file_name"],
            "document_id": c["document_id"],
            "page": (c.get("metadata") or {}).get("page"),
            "element_type": (c.get("metadata") or {}).get("element_type", "text"),
            "score": round(c.get("relevance_score", c.get("similarity", 0.0)), 4),
            "snippet": c["content"][:300],
        }
        for c in chunks
    ]


# =========================
# Query pipeline
# =========================

class QueryRun:
    """State of one query, shared between the streaming generator and the post-response task."""

    def __init__(self, user: User, request: QueryRequest):
        self.user = user
        self.request = request
        self.message_id = history.new_message_id()
        self.conversation_id = request.conversation_id or uuid.uuid4().hex
        self.started = time.perf_counter()
        self.answer = ""
        self.sources: List[dict] = []
        self.contexts: List[str] = []
        self.embedding: Optional[List[float]] = None
        self.cached = False
        self.web_search_used = False
        self.guardrail_intervened = False
        self.completed = False
        self.usage = bedrock.GenerationUsage()
        self.ttft_ms: Optional[float] = None
        self.retrieval_ms: Optional[float] = None

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000

    def meta(self) -> dict:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "cached": self.cached,
            "web_search_used": self.web_search_used,
            "sources": self.sources,
        }

    def events(self) -> Iterator[str]:
        question = self.request.question.strip()
        try:
            guarded = bedrock.apply_guardrail(question, "INPUT")
            if guarded.intervened:
                self.guardrail_intervened = True
                self.answer = guarded.text
                self.ttft_ms = self.elapsed_ms()
                yield sse("meta", self.meta())
                yield sse("token", {"text": self.answer})
                self.completed = True
                yield sse("done", self.done_payload())
                return

            self.embedding = bedrock.embed_query(question)

            hit = cache.lookup(question, self.embedding, self.user.principals)
            if hit:
                self.cached = True
                self.answer = hit["answer"]
                self.sources = hit.get("sources", [])
                self.ttft_ms = self.elapsed_ms()
                yield sse("meta", self.meta())
                yield sse("token", {"text": self.answer})
                self.completed = True
                yield sse("done", self.done_payload())
                return

            result = retrieval.retrieve(question, self.embedding, self.user.principals)
            self.retrieval_ms = result.latency_ms
            self.sources = source_summary(result.chunks)
            self.contexts = [c["content"] for c in result.chunks]

            web_results = None
            if result.is_weak and self.request.web_search:
                web_results = web_search.search(question)
                if web_results:
                    self.web_search_used = True
                    self.contexts.append(web_results)

            yield sse("meta", self.meta())

            checked_upto = 0
            for text in bedrock.stream_answer(question, result.chunks, web_results, self.usage):
                if self.ttft_ms is None:
                    self.ttft_ms = self.elapsed_ms()
                self.answer += text
                yield sse("token", {"text": text})

                # Check the answer so far every few hundred words, so a blocked answer is cut short
                if len(self.answer) - checked_upto >= settings.guardrail_check_chars:
                    checked_upto = len(self.answer)
                    if (replacement := self._check_output()) is not None:
                        yield sse("replace", {"text": replacement})
                        break

            if not self.guardrail_intervened and (replacement := self._check_output()) is not None:
                yield sse("replace", {"text": replacement})

            self.completed = True
            yield sse("done", self.done_payload())

        except Exception as e:
            logger.exception(f"Query {self.message_id} failed")
            emit_metrics({"QueryErrors": 1}, {"message_id": self.message_id, "error": type(e).__name__})
            yield sse("error", {"message": "Sorry, something went wrong answering that question. Please try again."})

    def _check_output(self) -> Optional[str]:
        guarded = bedrock.apply_guardrail(self.answer, "OUTPUT")
        if guarded.intervened:
            self.guardrail_intervened = True
            self.answer = guarded.text
            return guarded.text
        return None

    def cost_usd(self) -> float:
        return (self.usage.input_tokens * settings.price_input_per_mtok
                + self.usage.output_tokens * settings.price_output_per_mtok) / 1_000_000

    def done_payload(self) -> dict:
        return {
            "message_id": self.message_id,
            "latency_ms": round(self.elapsed_ms()),
            "time_to_first_token_ms": round(self.ttft_ms or 0),
            "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
            "model": self.usage.model or None,
            "guardrail_intervened": self.guardrail_intervened,
        }

    def finish(self) -> None:
        """Runs after the response is sent: metrics, cache, history, sampled evaluation."""
        if not self.completed:
            return
        latency_ms = self.elapsed_ms()
        metrics = {
            "QueryLatencyMs": latency_ms,
            "TimeToFirstTokenMs": self.ttft_ms or latency_ms,
            "CacheHit": 1.0 if self.cached else 0.0,
            "WebSearchFallback": 1.0 if self.web_search_used else 0.0,
            "InputTokens": self.usage.input_tokens,
            "OutputTokens": self.usage.output_tokens,
            "EstimatedCostUSD": self.cost_usd(),
        }
        if self.retrieval_ms is not None:
            metrics["RetrievalLatencyMs"] = self.retrieval_ms
        if self.guardrail_intervened:
            metrics["GuardrailIntervened"] = 1
        emit_metrics(metrics, {
            "message_id": self.message_id,
            "user_id": self.user.user_id,
            "model": self.usage.model,
            "stop_reason": self.usage.stop_reason,
        })

        if (not self.cached and not self.web_search_used and not self.guardrail_intervened
                and self.answer and self.embedding is not None):
            cache.store(self.request.question, self.embedding, self.user.principals,
                        {"answer": self.answer, "sources": self.sources})

        try:
            history.save_turn(
                self.user.user_id, self.message_id, self.conversation_id,
                self.request.question, self.answer, self.sources,
                {
                    "cached": self.cached,
                    "web_search_used": self.web_search_used,
                    "guardrail_intervened": self.guardrail_intervened,
                    "model": self.usage.model,
                    "latency_ms": round(latency_ms),
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                },
            )
        except Exception as e:
            logger.warning(f"Could not save history for {self.message_id}: {e}")

        if not self.cached and not self.guardrail_intervened and self.contexts and evaluation.should_sample():
            evaluation.evaluate_answer(self.user.user_id, self.message_id, self.request.question,
                                       self.answer, self.contexts)


# =========================
# Routes
# =========================

@app.get("/health")
def health():
    return {"status": "ok", "stage": settings.stage}


@app.post("/query")
def query(request: QueryRequest, user: User = Depends(get_current_user)):
    if cache.is_rate_limited(user.user_id):
        emit_metrics({"RateLimited": 1}, {"user_id": user.user_id})
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Rate limit is {settings.rate_limit_per_minute} questions per minute",
                            headers={"Retry-After": "60"})

    run = QueryRun(user, request)

    if request.stream:
        return StreamingResponse(
            run.events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(run.finish),
        )

    error = None
    done = {}
    for raw in run.events():
        event, _, data = raw.partition("\ndata: ")
        payload = json.loads(data)
        if event == "event: error":
            error = payload["message"]
        elif event == "event: done":
            done = payload
    if error:
        return JSONResponse({"message": error, "message_id": run.message_id}, status_code=500)
    return JSONResponse(
        {**run.meta(), "answer": run.answer, **done},
        background=BackgroundTask(run.finish),
    )


@app.get("/history")
def get_history(limit: int = Query(default=20, ge=1, le=100),
                conversation_id: Optional[str] = Query(default=None, max_length=64),
                user: User = Depends(get_current_user)):
    return {"items": history.list_turns(user.user_id, limit, conversation_id)}


@app.post("/feedback")
def feedback(request: FeedbackRequest, user: User = Depends(get_current_user)):
    if not history.record_feedback(user.user_id, request.message_id, request.rating, request.comment):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    emit_metrics({"UserFeedback": float(request.rating)}, {"message_id": request.message_id})
    return {"message": "Thanks for the feedback"}


SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]")
ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "csv"}


@app.post("/documents/upload-url")
def create_upload_url(request: UploadRequest, user: User = Depends(get_current_user)):
    """
    Return a presigned S3 POST for a direct upload. The object lands under
    uploads/{user}/{document_id}/ and S3 -> SQS -> ingestion picks it up. Users can
    share a document only with groups they belong to.
    """
    if user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service clients cannot upload documents")

    file_name = SAFE_FILE_NAME.sub("_", request.file_name).strip() or "document"
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    not_member = sorted(set(request.share_with_groups) - set(user.groups))
    if not_member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You are not a member of: {', '.join(not_member)}")

    document_id = str(uuid.uuid4())
    key = f"uploads/{user.user_id}/{document_id}/{file_name}"
    acl = ",".join(f"group:{g}" for g in sorted(set(request.share_with_groups)))

    post = s3().generate_presigned_post(
        Bucket=settings.documents_bucket,
        Key=key,
        Fields={"Content-Type": request.content_type, "x-amz-meta-acl": acl},
        Conditions=[
            {"Content-Type": request.content_type},
            {"x-amz-meta-acl": acl},
            ["content-length-range", 1, settings.max_upload_bytes],
        ],
        ExpiresIn=900,
    )

    metadata_table().put_item(Item={
        "id": f"doc#{document_id}",
        "document_id": document_id,
        "user_id": user.user_id,
        "file_name": file_name,
        "status": "uploading",
        "shared_with": sorted(set(request.share_with_groups)),
        "updated_at": int(time.time() * 1000),
    })

    return {"document_id": document_id, "upload": post, "max_bytes": settings.max_upload_bytes}


@app.get("/documents")
def list_documents(user: User = Depends(get_current_user)):
    """The caller's uploads and their ingestion status."""
    items = metadata_table().query(
        IndexName="UserIndex", KeyConditionExpression=Key("user_id").eq(user.user_id)).get("Items", [])
    fields = ("document_id", "file_name", "status", "num_chunks", "num_tables", "num_figures", "shared_with", "updated_at")
    documents = [{k: v for k, v in history.from_dynamo(i).items() if k in fields} for i in items]
    return {"items": sorted(documents, key=lambda d: d.get("updated_at", 0), reverse=True)}
