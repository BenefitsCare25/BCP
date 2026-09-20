"""Company notification preferences. Missing rows retain immediate delivery."""

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class WorkflowNotificationSettings(Base, TimestampMixin):
    __tablename__ = "workflow_notification_settings"
    __table_args__ = (
        CheckConstraint("claim_delivery IN ('immediate', 'digest')", name="delivery_valid"),
        CheckConstraint("digest_minutes BETWEEN 15 AND 1440", name="digest_interval_valid"),
    )

    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    claim_delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="immediate")
    digest_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
