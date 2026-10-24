"""
ElastiCache (Valkey) helpers: per-user rate limiting and a semantic answer cache.

Cached answers are scoped by the caller's ACL principals, so a cached answer is only
served to callers who can read exactly the same documents. Within a scope, a hit is
an exact (normalised) question match or a question whose embedding is within the
cosine-similarity threshold of a recent one. Both fail open if the cache is down.
"""
import hashlib
import json
import logging
import time
import uuid
from typing import List, Optional

import numpy as np
import redis

from .config import settings

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None


def client() -> Optional[redis.Redis]:
    global _client
    if _client is None and settings.redis_host:
        _client = redis.Redis(
            host=settings.redis_host,
            port=6379,
            ssl=settings.redis_tls,
            socket_timeout=0.5,
            socket_connect_timeout=1.0,
            health_check_interval=30,
        )
    return _client


def _prefix() -> str:
    return f"rag:{settings.stage}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def scope_for(principals: List[str]) -> str:
    return _hash(",".join(sorted(principals)))[:24]


def normalise(question: str) -> str:
    return " ".join(question.lower().split())


# =========================
# Rate limiting
# =========================

def is_rate_limited(user_id: str) -> bool:
    """Fixed one-minute window per user."""
    r = client()
    if r is None:
        return False
    key = f"{_prefix()}:rl:{user_id}:{int(time.time() // 60)}"
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 90)
        count, _ = pipe.execute()
        return count > settings.rate_limit_per_minute
    except redis.RedisError as e:
        logger.warning(f"Rate limiter unavailable, allowing request: {e}")
        return False


# =========================
# Semantic cache
# =========================

def lookup(question: str, embedding: List[float], principals: List[str]) -> Optional[dict]:
    r = client()
    if r is None:
        return None
    scope = scope_for(principals)
    base = f"{_prefix()}:cache:{scope}"
    try:
        exact = r.get(f"{base}:exact:{_hash(normalise(question))}")
        if exact:
            return json.loads(exact)

        cutoff = time.time() - settings.cache_ttl_seconds
        ids = r.zrevrangebyscore(f"{base}:index", "+inf", cutoff, start=0, num=settings.semantic_cache_max_entries)
        if not ids:
            return None
        vectors = r.hmget(f"{base}:vectors", ids)

        query = np.asarray(embedding, dtype=np.float32)
        query /= (np.linalg.norm(query) or 1.0)
        best_id, best_score = None, -1.0
        for entry_id, raw in zip(ids, vectors):
            if raw is None:
                continue
            candidate = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
            score = float(candidate @ query)  # stored vectors are unit length
            if score > best_score:
                best_id, best_score = entry_id, score

        if best_id is not None and best_score >= settings.semantic_cache_threshold:
            answer = r.hget(f"{base}:answers", best_id)
            if answer:
                result = json.loads(answer)
                result["cache_similarity"] = round(best_score, 4)
                return result
    except redis.RedisError as e:
        logger.warning(f"Cache lookup failed: {e}")
    return None


def store(question: str, embedding: List[float], principals: List[str], answer: dict) -> None:
    r = client()
    if r is None:
        return
    scope = scope_for(principals)
    base = f"{_prefix()}:cache:{scope}"
    ttl = settings.cache_ttl_seconds
    entry_id = uuid.uuid4().hex
    now = time.time()

    vector = np.asarray(embedding, dtype=np.float32)
    vector /= (np.linalg.norm(vector) or 1.0)
    payload = json.dumps(answer)

    try:
        stale = r.zrangebyscore(f"{base}:index", "-inf", now - ttl)
        pipe = r.pipeline()
        pipe.set(f"{base}:exact:{_hash(normalise(question))}", payload, ex=ttl)
        pipe.zadd(f"{base}:index", {entry_id: now})
        pipe.hset(f"{base}:vectors", entry_id, vector.astype(np.float16).tobytes())
        pipe.hset(f"{base}:answers", entry_id, payload)
        if stale:
            pipe.zrem(f"{base}:index", *stale)
            pipe.hdel(f"{base}:vectors", *stale)
            pipe.hdel(f"{base}:answers", *stale)
        for key in ("index", "vectors", "answers"):
            pipe.expire(f"{base}:{key}", ttl)
        pipe.execute()
    except redis.RedisError as e:
        logger.warning(f"Cache store failed: {e}")
