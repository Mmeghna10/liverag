"""Your own account: sign up, see and edit your profile, change your password."""

import re

from fastapi import APIRouter, HTTPException, Request, status
from psycopg.errors import UniqueViolation

from app.auth import CurrentUserDep, hasher, issue_token, password_ok
from app.schemas import (MeResponse, PasswordChange, ProfileUpdate, SignupRequest,
                         TokenResponse)

router = APIRouter(prefix="/api/v1", tags=["account"])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, request: Request) -> TokenResponse:
    """Create an account and sign in. New accounts start with no groups: public data only."""
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address")
    try:
        with request.app.state.source_pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO users (email, display_name, password_hash) VALUES (%s, %s, %s) "
                "RETURNING id", (email, body.display_name.strip(), hasher.hash(body.password)),
            ).fetchone()
    except UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An account with that email already exists") from None
    return issue_token(row[0], email)


@router.get("/me")
def me(request: Request, user: CurrentUserDep) -> MeResponse:
    with request.app.state.source_pool.connection() as conn:
        name, created = conn.execute("SELECT display_name, created_at FROM users WHERE id = %s",
                                     (user.id,)).fetchone()
    with request.app.state.index_pool.connection() as conn:
        chats, questions = conn.execute("""
            SELECT count(DISTINCT c.id), count(m.id) FILTER (WHERE m.role = 'user')
            FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.user_id = %s""", (user.id,)).fetchone()
    return MeResponse(id=user.id, email=user.email, groups=user.groups, display_name=name,
                      created_at=created, conversations=chats, questions=questions)


@router.patch("/me")
def update_me(body: ProfileUpdate, request: Request, user: CurrentUserDep) -> MeResponse:
    with request.app.state.source_pool.connection() as conn:
        conn.execute("UPDATE users SET display_name = %s WHERE id = %s",
                     (body.display_name.strip(), user.id))
    return me(request, user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, request: Request, user: CurrentUserDep) -> None:
    with request.app.state.source_pool.connection() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id = %s",
                              (user.id,)).fetchone()[0]
        if not password_ok(stored, body.current_password):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is wrong")
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (hasher.hash(body.new_password), user.id))
