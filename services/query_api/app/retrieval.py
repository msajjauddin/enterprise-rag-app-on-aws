"""
Hybrid retrieval with document-level access control.

Vector (HNSW, cosine) and full-text (tsvector) candidates are restricted to documents
whose ACL contains one of the caller's principals, fused with reciprocal rank fusion,
then reranked with a Bedrock rerank model.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List

from . import bedrock
from .config import settings
from .db import read_pool

logger = logging.getLogger(__name__)

RRF_K = 60

HYBRID_SEARCH_SQL = """
WITH allowed AS (
    SELECT DISTINCT document_id FROM document_acl WHERE principal = ANY(%(principals)s)
),
vec AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY c.embedding <=> %(qv)s::vector) AS rank
    FROM chunks c
    WHERE c.document_id IN (SELECT document_id FROM allowed)
    ORDER BY c.embedding <=> %(qv)s::vector
    LIMIT %(k)s
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, q) DESC) AS rank
    FROM chunks c, websearch_to_tsquery('english', %(query)s) q
    WHERE c.content_tsv @@ q
      AND c.document_id IN (SELECT document_id FROM allowed)
    ORDER BY ts_rank_cd(c.content_tsv, q) DESC
    LIMIT %(k)s
)
SELECT c.chunk_id, c.document_id, c.content, c.metadata, d.file_name,
       1 - (c.embedding <=> %(qv)s::vector) AS similarity,
       COALESCE(1.0 / (%(rrf_k)s + vec.rank), 0) + COALESCE(1.0 / (%(rrf_k)s + kw.rank), 0) AS rrf_score
FROM (SELECT id FROM vec UNION SELECT id FROM kw) ids
JOIN chunks c ON c.id = ids.id
JOIN documents d ON d.document_id = c.document_id
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN kw ON kw.id = c.id
ORDER BY rrf_score DESC
LIMIT %(k)s
"""


@dataclass
class RetrievalResult:
    chunks: List[dict] = field(default_factory=list)
    top_score: float = 0.0
    reranked: bool = False
    latency_ms: float = 0.0

    @property
    def is_weak(self) -> bool:
        return not self.chunks or self.top_score < settings.rag_confidence_threshold


def to_vector_literal(embedding: List[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


def hybrid_search(query: str, query_embedding: List[float], principals: List[str], k: int) -> List[dict]:
    params = {
        "principals": principals,
        "qv": to_vector_literal(query_embedding),
        "query": query,
        "k": k,
        "rrf_k": RRF_K,
    }
    with read_pool().connection() as conn:
        rows = conn.execute(HYBRID_SEARCH_SQL, params).fetchall()

    return [
        {
            "chunk_id": r[0],
            "document_id": r[1],
            "content": r[2],
            "metadata": r[3] or {},
            "file_name": r[4],
            "similarity": float(r[5]),
            "rrf_score": float(r[6]),
        }
        for r in rows
    ]


def retrieve(query: str, query_embedding: List[float], principals: List[str]) -> RetrievalResult:
    start = time.perf_counter()
    candidates = hybrid_search(query, query_embedding, principals, settings.candidate_k)

    ranking = bedrock.rerank(query, [c["content"] for c in candidates], settings.final_k)
    if ranking is not None:
        chunks = []
        for index, score in ranking:
            chunk = dict(candidates[index])
            chunk["relevance_score"] = score
            chunks.append(chunk)
        top_score = chunks[0]["relevance_score"] if chunks else 0.0
        reranked = True
    else:
        chunks = candidates[:settings.final_k]
        top_score = max((c["similarity"] for c in chunks), default=0.0)
        reranked = False

    return RetrievalResult(
        chunks=chunks,
        top_score=top_score,
        reranked=reranked,
        latency_ms=(time.perf_counter() - start) * 1000,
    )
