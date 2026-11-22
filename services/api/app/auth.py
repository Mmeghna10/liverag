"""Login with email + password (checked against the source DB's users) and JWT bearer tokens."""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from liverag_core.retrieval import user_groups

from app.schemas import CurrentUser, LoginRequest, TokenResponse

ALGORITHM = "HS256"
TOKEN_MINUTES = 60

ADMIN_GROUP_ID = 5  # "admins" in the source DB seed

# uvicorn only prints its own loggers at INFO level, so we log through one of them.
log = logging.getLogger("uvicorn.error")

hasher = PasswordHasher()
bearer = HTTPBearer()
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def password_ok(stored_hash: str, password: str) -> bool:
    try:
        return hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@router.post("/login")
def login(body: LoginRequest, request: Request) -> TokenResponse:
    with request.app.state.source_pool.connection() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = %s AND is_active",
            (body.email.strip().lower(),),
        ).fetchone()
    # Same answer for "no such user" and "wrong password", so attackers can't probe for emails.
    if row is None or not password_ok(row[1], body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return issue_token(row[0], body.email.strip().lower())


def issue_token(user_id: UUID, email: str) -> TokenResponse:
    """A signed token that proves who you are for the next TOKEN_MINUTES minutes."""
    expires = datetime.now(UTC) + timedelta(minutes=TOKEN_MINUTES)
    token = jwt.encode({"sub": str(user_id), "email": email, "exp": expires},
                       os.environ["JWT_SECRET"], algorithm=ALGORITHM)
    return TokenResponse(access_token=token, expires_in=TOKEN_MINUTES * 60)


def current_user(request: Request,
                 credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]
                 ) -> CurrentUser:
    """Dependency: decode the bearer token and load the user's groups."""
    try:
        # Checks the signature (nobody can forge or edit a token) and the expiry time.
        claims = jwt.decode(credentials.credentials, os.environ["JWT_SECRET"],
                            algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from None

    user_id = UUID(claims["sub"])
    # Groups are read on every request instead of being stored in the token,
    # so a revoked group takes effect at once, not when the token expires.
    with request.app.state.index_pool.connection() as conn:
        groups = user_groups(conn, user_id)
    return CurrentUser(id=user_id, email=claims["email"], groups=groups)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]




def require_admin(user: CurrentUser) -> None:
    if ADMIN_GROUP_ID not in user.groups:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")


def effective_user(request: Request, user: CurrentUser, view_as: str | None) -> CurrentUser:
    """Who the answer is for: normally the caller; for admins, optionally another user ("view as").

    The check happens on the server on every request. The UI hiding the picker is not security.
    """
    if not view_as or view_as == user.email:
        return user
    require_admin(user)
    with request.app.state.source_pool.connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s AND is_active",
                           (view_as,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    with request.app.state.index_pool.connection() as conn:
        groups = user_groups(conn, row[0])
    log.info("view_as: %s is viewing as %s", user.email, view_as)  # audit trail
    return CurrentUser(id=row[0], email=view_as, groups=groups)
