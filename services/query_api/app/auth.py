"""
JWT validation against the Cognito user pool. The access token identifies the user
(sub) and carries their groups (cognito:groups, filled from the corporate IdP by the
pre-token-generation Lambda). Those become the ACL principals for retrieval.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

import jwt
from fastapi import Header, HTTPException, status

from .config import settings

logger = logging.getLogger(__name__)

EVAL_SCOPE = "rag-api/eval"

_jwks_client: Optional[jwt.PyJWKClient] = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{settings.cognito_issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks_client


@dataclass(frozen=True)
class User:
    user_id: str
    groups: List[str]
    email: Optional[str] = None
    is_service: bool = False

    @property
    def principals(self) -> List[str]:
        """ACL principals this caller may read documents as."""
        return [f"user:{self.user_id}"] + [f"group:{g}" for g in self.groups]


def user_from_claims(claims: dict) -> User:
    """
    Map validated access-token claims to a caller. User tokens come from the UI
    client; client-credentials tokens from the CI evaluation client.
    """
    if claims.get("token_use") != "access":
        raise ValueError("not an access token")

    client_id = claims.get("client_id")
    if client_id == settings.cognito_app_client_id and claims.get("sub"):
        return User(
            user_id=claims["sub"],
            groups=sorted(set(claims.get("cognito:groups", []))),
            email=claims.get("username"),
        )

    if settings.cognito_eval_client_id and client_id == settings.cognito_eval_client_id:
        scopes = (claims.get("scope") or "").split()
        if EVAL_SCOPE not in scopes:
            raise ValueError("eval client token without eval scope")
        return User(user_id="service-eval", groups=list(settings.eval_groups), is_service=True)

    raise ValueError("token issued to an unknown client")


def decode_token(token: str) -> dict:
    signing_key = _jwks().get_signing_key_from_jwt(token)
    # Cognito access tokens carry client_id instead of aud; checked in user_from_claims
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=settings.cognito_issuer,
        options={"verify_aud": False, "require": ["exp", "iat", "sub", "token_use"]},
        leeway=30,
    )


def get_current_user(authorization: str = Header(default="")) -> User:
    """FastAPI dependency: validate the bearer token and return the caller."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return user_from_claims(decode_token(token))
    except (jwt.PyJWTError, ValueError) as e:
        logger.info(f"Rejected token: {e}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
