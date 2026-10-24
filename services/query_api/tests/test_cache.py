"""Tests for the semantic cache and rate limiter (fakeredis)."""
import numpy as np

from app import cache


def unit(*values):
    v = np.zeros(8, dtype=np.float32)
    v[:len(values)] = values
    return (v / np.linalg.norm(v)).tolist()


PRINCIPALS = ["user:u1", "group:finance"]


def test_exact_match_hit_after_store():
    cache.store("What is the travel policy?", unit(1, 0), PRINCIPALS, {"answer": "A", "sources": []})
    hit = cache.lookup("  what is the TRAVEL policy? ", unit(0, 1), PRINCIPALS)
    assert hit["answer"] == "A"


def test_semantic_match_above_threshold():
    cache.store("How many vacation days do I get?", unit(1, 0.01), PRINCIPALS, {"answer": "25 days", "sources": []})
    hit = cache.lookup("How much annual leave do I have?", unit(1, 0.02), PRINCIPALS)
    assert hit["answer"] == "25 days"
    assert hit["cache_similarity"] >= 0.95


def test_semantic_miss_below_threshold():
    cache.store("How many vacation days do I get?", unit(1, 0), PRINCIPALS, {"answer": "25 days", "sources": []})
    assert cache.lookup("Where is the Pune office?", unit(0, 1), PRINCIPALS) is None


def test_cache_is_scoped_by_principals():
    cache.store("Q3 revenue?", unit(1, 0), ["user:u1", "group:finance"], {"answer": "secret", "sources": []})
    assert cache.lookup("Q3 revenue?", unit(1, 0), ["user:u2", "group:engineering"]) is None
    # Scope ignores principal order
    assert cache.lookup("Q3 revenue?", unit(1, 0), ["group:finance", "user:u1"])["answer"] == "secret"


def test_rate_limit_after_threshold():
    # RATE_LIMIT_PER_MINUTE=3 in conftest
    assert [cache.is_rate_limited("u1") for _ in range(4)] == [False, False, False, True]
    assert cache.is_rate_limited("u2") is False


def test_fails_open_without_redis(monkeypatch):
    monkeypatch.setattr(cache, "client", lambda: None)
    assert cache.is_rate_limited("u1") is False
    assert cache.lookup("q", unit(1), PRINCIPALS) is None
    cache.store("q", unit(1), PRINCIPALS, {"answer": "a"})
