from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import AuditLog


def log_action(db: Session, actor_id: str | None, actor_name: str, action: str, entity_type: str,
               entity_id: str | None, detail: dict | None = None, ip: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, actor_name=actor_name, action=action, entity_type=entity_type,
                    entity_id=entity_id, detail=detail, ip=ip))
    db.commit()
