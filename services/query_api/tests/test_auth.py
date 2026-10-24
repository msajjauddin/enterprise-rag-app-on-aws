"""Tests for access-token claim mapping and the auth dependency."""
import pytest
from fastapi import HTTPException

from app.auth import User, get_current_user, user_from_claims


def test_ui_token_maps_user_and_groups():
    user = user_from_claims({
        "token_use": "access",
        "client_id": "ui-client",
        "sub": "user-123",
        "username": "jane@example.com",
        "cognito:groups": ["finance", "all-staff", "finance"],
    })
    assert user.user_id == "user-123"
    assert user.groups == ["all-staff", "finance"]
    assert user.principals == ["user:user-123", "group:all-staff", "group:finance"]
    assert not user.is_service


def test_eval_client_requires_eval_scope():
    user = user_from_claims({"token_use": "access", "client_id": "eval-client", "sub": "eval-client",
                             "scope": "rag-api/eval"})
    assert user.is_service
    assert user.principals == ["user:service-eval", "group:eval"]

    with pytest.raises(ValueError):
        user_from_claims({"token_use": "access", "client_id": "eval-client", "sub": "eval-client", "scope": ""})


@pytest.mark.parametrize("claims", [
    {"token_use": "id", "client_id": "ui-client", "sub": "u"},
    {"token_use": "access", "client_id": "someone-else", "sub": "u"},
])
def test_rejects_id_tokens_and_unknown_clients(claims):
    with pytest.raises(ValueError):
        user_from_claims(claims)


def test_missing_bearer_token_is_401():
    with pytest.raises(HTTPException) as exc:
        get_current_user(authorization="")
    assert exc.value.status_code == 401


def test_invalid_token_is_401(monkeypatch):
    import jwt
    from app import auth

    def boom(token):
        raise jwt.InvalidTokenError("bad")

    monkeypatch.setattr(auth, "decode_token", boom)
    with pytest.raises(HTTPException) as exc:
        get_current_user(authorization="Bearer abc")
    assert exc.value.status_code == 401


def test_user_principals_without_groups():
    assert User(user_id="u1", groups=[]).principals == ["user:u1"]
