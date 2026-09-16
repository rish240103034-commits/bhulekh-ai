from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.entities import AuditLog, User
from app.schemas.schemas import AuditOut, DashboardStats
from app.services.stats import dashboard_stats

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/stats", response_model=DashboardStats)
def stats(state: str | None = None, district: str | None = None,
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role in ("operator", "verifier"):
        state = user.state or state
        district = user.district or district
    return dashboard_stats(db, state, district)


@router.get("/audit", response_model=list[AuditOut])
def audit(entity_id: str | None = None, action: str | None = None, limit: int = Query(100, le=1000),
          db: Session = Depends(get_db), user: User = Depends(require_roles("verifier"))):
    q = db.query(AuditLog)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if action:
        q = q.filter(AuditLog.action.ilike(f"{action}%"))
    return q.order_by(AuditLog.created_at.desc()).limit(limit).all()
