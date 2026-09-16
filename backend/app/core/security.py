"""Authentication and authorisation: password hashing, JWT access/refresh tokens, RBAC.

Design notes
------------
* Access tokens are short-lived (minutes) and carry the role, so authorisation needs
  no database read on the hot path. Refresh tokens are long-lived, stored hashed in the
  database and revocable, so a stolen refresh token can be cut off and a single logout
  or a disabled account invalidates future refreshes.
* Every token carries a ``type`` claim (``access`` / ``refresh``) and a unique ``jti``;
  an access token presented to the refresh endpoint (or vice-versa) is rejected.
* Password verification runs even for unknown usernames (a dummy hash) so login timing
  does not reveal whether a username exists.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

pwd_context = CryptContext(schemes=[settings.password_scheme], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")

# A precomputed hash used to equalise login timing for unknown users.
_DUMMY_HASH = pwd_context.hash("timing-equalisation-placeholder")

# Role hierarchy: admin > verifier > operator > viewer. `integration` is a service
# role scoped to the integration APIs and is intentionally NOT part of the hierarchy.
ROLE_RANK = {"viewer": 0, "operator": 1, "verifier": 2, "admin": 3}
VALID_ROLES = {"admin", "verifier", "operator", "viewer", "integration"}


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def verify_password_constant_time(password: str, hashed: str | None) -> bool:
    """Verify a password, spending the same work whether or not the user exists."""
    return pwd_context.verify(password, hashed or _DUMMY_HASH) and hashed is not None


def validate_password_strength(password: str) -> None:
    """Reject weak passwords. Raises HTTP 422 with a specific reason."""
    reasons: list[str] = []
    if len(password) < settings.password_min_length:
        reasons.append(f"at least {settings.password_min_length} characters")
    if not any(c.islower() for c in password):
        reasons.append("a lowercase letter")
    if not any(c.isupper() for c in password):
        reasons.append("an uppercase letter")
    if not any(c.isdigit() for c in password):
        reasons.append("a digit")
    if reasons:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Password must contain " + ", ".join(reasons) + ".")


# --------------------------------------------------------------------------- tokens
def _encode(subject: str, role: str, token_type: str, lifetime: timedelta,
            extra: dict | None = None) -> tuple[str, str]:
    """Return (encoded_jwt, jti)."""
    now = datetime.now(UTC)
    jti = secrets.token_urlsafe(16)
    payload = {"sub": subject, "role": role, "type": token_type, "jti": jti,
               "iat": now, "exp": now + lifetime}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm), jti


def create_access_token(subject: str, role: str) -> str:
    token, _ = _encode(subject, role, "access",
                       timedelta(minutes=settings.access_token_expire_minutes))
    return token


def create_refresh_token(subject: str, role: str) -> tuple[str, str]:
    """Return (refresh_token, jti). The jti is stored server-side for revocation."""
    return _encode(subject, role, "refresh", timedelta(days=settings.refresh_token_expire_days))


def hash_token(token: str) -> str:
    """Store refresh tokens hashed, so a database leak does not hand over live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str, expected_type: str) -> dict:
    cred_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                             headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        raise cred_exc from None
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise cred_exc
    return payload


# --------------------------------------------------------------------------- dependencies
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    from app.models.entities import User  # local import avoids a circular import

    payload = decode_token(token, "access")
    user = db.query(User).filter(User.username == payload["sub"],
                                 User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require_roles(*roles: str):
    """Allow the listed roles, or any hierarchy role that outranks the lowest listed one.

    The service role ``integration`` is granted only by explicit listing, never by rank,
    so a broad ``require_roles("operator")`` never accidentally admits a service account.
    """
    ranked = [r for r in roles if r in ROLE_RANK]
    min_rank = min((ROLE_RANK[r] for r in ranked), default=None)

    def checker(user=Depends(get_current_user)):
        if user.role in roles:
            return user
        if min_rank is not None and user.role in ROLE_RANK and ROLE_RANK[user.role] >= min_rank:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Role '{user.role}' is not permitted to perform this action")

    return checker
