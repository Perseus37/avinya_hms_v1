from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, event, func
from sqlalchemy.orm import Mapped, Session as SQLAlchemySession, mapped_column


class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


@event.listens_for(SQLAlchemySession, "before_flush")
def stamp_audit_actor(session, flush_context, instances):
    """Apply the current session audit actor to new or changed audited entities."""
    actor_id = session.info.get("audit_user_id")
    if actor_id is None:
        return

    for entity in session.new:
        if isinstance(entity, AuditMixin):
            if entity.created_by_id is None:
                entity.created_by_id = actor_id
            entity.updated_by_id = actor_id

    for entity in session.dirty:
        if isinstance(entity, AuditMixin) and session.is_modified(
            entity, include_collections=False
        ):
            entity.updated_by_id = actor_id
