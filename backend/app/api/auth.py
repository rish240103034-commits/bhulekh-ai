"""Authentication API: login, token refresh, logout, and admin user management."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limit
from app.core.security import (
    VALID_ROLES,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    hash_token,
    require_roles,
    validate_password_strength,
    verify_password_constant_time,
)
from app.models.entities import RefreshToken, User
from app.schemas.schemas import RefreshIn, Token, UserCreate, UserOut
from app.services.audit import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _issue_tokens(db: Session, user: User, request: Request) -> Token:
    """Mint an access token and a persisted, revocable refresh token."""
    from datetime import timedelta

    access = create_access_token(user.username, user.role)
    refresh, jti = create_refresh_token(user.username, user.role)
    db.add(RefreshToken(
        jti=jti, token_hash=hash_token(refresh), user_id=user.id,
        user_agent=(request.headers.get("user-agent") or "")[:256], ip=_client_ip(request),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)))
    db.commit()
    return Token(access_token=access, refresh_token=refresh, role=user.role,
                 username=user.username, full_name=user.full_name,
                 expires_in=settings.access_token_expire_minutes * 60)


@router.post("/login", response_model=Token)
@limit(settings.rate_limit_login)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    # Constant-time verify runs even when the user is unknown, so login timing does not
    # reveal whether a username exists.
    ok = verify_password_constant_time(form.password, user.hashed_password if user else None)
    if not user or not ok or not user.is_active:
        log_action(db, None, form.username, "auth.login_failed", "user", None,
                   ip=_client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    log_action(db, user.id, user.username, "auth.login", "user", user.id, ip=_client_ip(request))
    return _issue_tokens(db, user, request)


@router.post("/refresh", response_model=Token)
@limit(settings.rate_limit_login)
def refresh(request: Request, body: RefreshIn, db: Session = Depends(get_db)):
    """Exchange a valid, unrevoked refresh token for a new token pair (rotation).

    The presented token is verified, looked up by hash, checked for revocation and
    expiry, then revoked and replaced. Reusing a rotated token fails, which detects
    token theft.
    """
    payload = decode_token(body.refresh_token, "refresh")
    stored = db.query(RefreshToken).filter(RefreshToken.jti == payload["jti"]).first()
    now = datetime.now(UTC)
    invalid = (stored is None or stored.revoked
               or stored.token_hash != hash_token(body.refresh_token)
               or stored.expires_at.replace(tzinfo=UTC) < now)
    if invalid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = db.query(User).filter(User.username == payload["sub"],
                                 User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")
    stored.revoked = True                       # rotate: single-use refresh tokens
    db.commit()
    return _issue_tokens(db, user, request)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revoke the presented refresh token (best-effort; always succeeds for the caller)."""
    try:
        payload = decode_token(body.refresh_token, "refresh")
        stored = db.query(RefreshToken).filter(RefreshToken.jti == payload["jti"],
                                               RefreshToken.user_id == user.id).first()
        if stored:
            stored.revoked = True
            db.commit()
    except HTTPException:
        pass
    log_action(db, user.id, user.username, "auth.logout", "user", user.id)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                admin: User = Depends(require_roles("admin"))):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Unknown role '{body.role}'")
    validate_password_strength(body.password)
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "Username already exists")
    u = User(username=body.username, full_name=body.full_name, role=body.role, state=body.state,
             district=body.district, hashed_password=hash_password(body.password))
    db.add(u)
    db.commit()
    db.refresh(u)
    log_action(db, admin.id, admin.username, "user.created", "user", u.id, {"role": u.role})
    return u


@router.patch("/users/{user_id}/toggle", response_model=UserOut)
def toggle_user(user_id: str, db: Session = Depends(get_db),
                admin: User = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == admin.id:
        raise HTTPException(400, "You cannot deactivate your own account")
    u.is_active = not u.is_active
    if not u.is_active:
        # Deactivating a user cuts off their refresh tokens so existing sessions die.
        db.query(RefreshToken).filter(RefreshToken.user_id == u.id,
                                      RefreshToken.revoked.is_(False)).update({"revoked": True})
    db.commit()
    log_action(db, admin.id, admin.username, "user.toggled", "user", u.id, {"active": u.is_active})
    return u
