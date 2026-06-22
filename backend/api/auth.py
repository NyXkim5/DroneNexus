"""
OVERWATCH JWT Authentication Module

Provides login, token validation, and role-based access control.
Auth is controlled by OverwatchSettings.auth_enabled (default: False).
When disabled, all endpoints are open -- existing tests are unaffected.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from config import DEFAULT_JWT_SECRET, OverwatchSettings

logger = logging.getLogger("overwatch.auth")

# ---------------------------------------------------------------------------
# Configuration (loaded from OverwatchSettings / env vars)
# ---------------------------------------------------------------------------
_settings = OverwatchSettings()

SECRET_KEY: str = _settings.jwt_secret
ALGORITHM: str = _settings.jwt_algorithm
TOKEN_EXPIRE_MINUTES: int = _settings.jwt_expire_minutes

if SECRET_KEY == DEFAULT_JWT_SECRET:
    logger.warning(
        "JWT secret is the default value. "
        "Set OVERWATCH_JWT_SECRET to a strong random string in production."
    )


# ---------------------------------------------------------------------------
# Password hashing helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Hash password with bcrypt (salted, key-stretched)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# Default user store (bcrypt hashed). Default credentials for development only.
# Set OVERWATCH_USERS in production.
# ---------------------------------------------------------------------------
_USERS: dict[str, dict] = {
    "operator": {"password_hash": _hash_password("nexus-alpha"), "role": "operator"},
    "viewer":   {"password_hash": _hash_password("nexus-view"),  "role": "viewer"},
}
_USING_DEFAULT_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class UserModel(BaseModel):
    username: str
    role: str  # "operator" | "viewer"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in: int  # seconds


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def create_access_token(username: str, role: str) -> str:
    """Create a signed JWT containing username and role."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


# ---------------------------------------------------------------------------
# Auth-enabled flag (read at import time, but the dependency re-checks at
# request time so hot-reloads via settings work).
# ---------------------------------------------------------------------------

def _auth_enabled() -> bool:
    """Return True when JWT enforcement is on. Fail-closed on error."""
    try:
        return OverwatchSettings().auth_enabled
    except Exception:
        return True


# ---------------------------------------------------------------------------
# FastAPI security scheme — extracts token from header *or* query param.
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer(auto_error=False)


async def _extract_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    token: Optional[str] = Query(None, alias="token"),
) -> Optional[str]:
    """
    Pull the JWT from:
      1. Authorization: Bearer <token>   (REST)
      2. ?token=<token>                  (WebSocket / browser)
    Returns None when no token is supplied.
    """
    if credentials and credentials.credentials:
        return credentials.credentials
    if token:
        return token
    return None


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# ---------------------------------------------------------------------------

async def get_current_user(
    raw_token: Optional[str] = Depends(_extract_token),
) -> Optional[UserModel]:
    """
    Validate the JWT and return a UserModel.
    - If auth is disabled, returns a synthetic operator user (full access).
    - If auth is enabled but no token is provided, raises 401.
    """
    if not _auth_enabled():
        # Auth disabled — everything is open; return a default operator
        return UserModel(username="__anonymous__", role="operator")

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(raw_token)
    return UserModel(username=payload["sub"], role=payload["role"])


# ---------------------------------------------------------------------------
# Dependency: require_operator
# ---------------------------------------------------------------------------

async def require_operator(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Raise 403 if the authenticated user is not an operator."""
    if user.role != "operator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator role required",
        )
    return user


# ---------------------------------------------------------------------------
# Dependency: require_viewer (viewer OR operator)
# ---------------------------------------------------------------------------

async def require_viewer(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Raise 403 if the user has no recognised role."""
    if user.role not in ("operator", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer role required",
        )
    return user


# ---------------------------------------------------------------------------
# Login rate limiting (in-memory, per IP)
# ---------------------------------------------------------------------------
_login_attempts: dict[str, tuple[int, float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes


def _check_rate_limit(ip: str) -> bool:
    """Return True if the login attempt should be allowed."""
    now = time.time()
    if ip in _login_attempts:
        count, first = _login_attempts[ip]
        if now - first > LOGIN_WINDOW_SECONDS:
            _login_attempts[ip] = (1, now)
            return True
        if count >= MAX_LOGIN_ATTEMPTS:
            return False
        _login_attempts[ip] = (count + 1, first)
    else:
        _login_attempts[ip] = (1, now)
    return True


# ---------------------------------------------------------------------------
# Auth router (mounted at /api/v1/auth)
# ---------------------------------------------------------------------------
auth_router = APIRouter(tags=["auth"])


@auth_router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, req: Request):
    """Authenticate with username/password and receive a JWT."""
    if not _check_rate_limit(req.client.host if req.client else "unknown"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )

    user_record = _USERS.get(body.username)
    if not user_record or not _verify_password(body.password, user_record["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(body.username, user_record["role"])
    expires_in = TOKEN_EXPIRE_MINUTES * 60  # seconds

    logger.info(f"Login: user={body.username} role={user_record['role']}")

    return TokenResponse(
        access_token=token,
        role=user_record["role"],
        expires_in=expires_in,
    )


@auth_router.get("/me")
async def whoami(user: UserModel = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return user
