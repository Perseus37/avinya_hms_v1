from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base


class ServiceTypeEnum(str, PyEnum):
    LAB = "LAB"
    RADIOLOGY = "RADIOLOGY"
    PROCEDURE = "PROCEDURE"
    PACKAGE = "PACKAGE"
    CONSULTATION = "CONSULTATION"


class OrderStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskStatusEnum(str, PyEnum):
    PENDING = "PENDING"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED"
    SAMPLE_REJECTED = "SAMPLE_REJECTED"
    IN_PROGRESS = "IN_PROGRESS"
    RESULT_ENTERED = "RESULT_ENTERED"
    VERIFIED = "VERIFIED"
    CANCELLED = "CANCELLED"


class ServiceCategory(AuditMixin, Base):
    __tablename__ = "service_categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    department: Mapped["Department"] = relationship()
    services: Mapped[List["ServiceCatalog"]] = relationship(back_populates="category")


class ServiceCatalog(AuditMixin, Base):
    __tablename__ = "service_catalog"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("service_categories.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[ServiceTypeEnum] = mapped_column(nullable=False)
    base_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    loinc_code: Mapped[Optional[str]] = mapped_column(String(50))
    cpt_code: Mapped[Optional[str]] = mapped_column(String(50))
    is_order_set: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    category: Mapped[ServiceCategory] = relationship(back_populates="services")


class ServiceBundleMapping(AuditMixin, Base):
    __tablename__ = "service_bundle_mappings"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_bundle_mapping_qty_pos"),
    )

    parent_service_id: Mapped[int] = mapped_column(
        ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True
    )
    child_service_id: Mapped[int] = mapped_column(
        ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    child_service: Mapped["ServiceCatalog"] = relationship(
        "ServiceCatalog", foreign_keys=[child_service_id]
    )


class DiagnosticOrder(AuditMixin, Base):
    __tablename__ = "diagnostic_orders"
    __table_args__ = (Index("ix_diagnostic_orders_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    ordering_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    clinical_notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[OrderStatusEnum] = mapped_column(
        default=OrderStatusEnum.ACTIVE, nullable=False
    )

    patient: Mapped["Patient"] = relationship()
    ordering_doctor: Mapped["User"] = relationship()
    tasks: Mapped[List["DiagnosticTask"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class DiagnosticTask(AuditMixin, Base):
    __tablename__ = "diagnostic_tasks"
    __table_args__ = (Index("ix_diagnostic_tasks_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_orders.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id"), nullable=False)
    status: Mapped[TaskStatusEnum] = mapped_column(
        default=TaskStatusEnum.PENDING, nullable=False
    )
    result_value: Mapped[Optional[str]] = mapped_column(Text)
    result_notes: Mapped[Optional[str]] = mapped_column(Text)
    is_critical_panic: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    performed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

    order: Mapped[DiagnosticOrder] = relationship(back_populates="tasks")
    service: Mapped[ServiceCatalog] = relationship()
    performed_by: Mapped[Optional["User"]] = relationship(
        foreign_keys=[performed_by_id]
    )
    verified_by: Mapped[Optional["User"]] = relationship(
        foreign_keys=[verified_by_id]
    )
