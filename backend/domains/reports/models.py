from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base



class ReportTypeEnum(str, PyEnum):
    OPD_APPOINTMENT_SLIP = "OPD_APPOINTMENT_SLIP"
    INVOICE = "INVOICE"
    PRESCRIPTION = "PRESCRIPTION"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    MLC_POLICE_REPORT = "MLC_POLICE_REPORT"
    SURGICAL_OPERATIVE_NOTE = "SURGICAL_OPERATIVE_NOTE"
    BIRTH_CERTIFICATE = "BIRTH_CERTIFICATE"
    DEATH_CERTIFICATE_MCCD = "DEATH_CERTIFICATE_MCCD"
    INSURANCE_PREAUTH = "INSURANCE_PREAUTH"
    INSURANCE_CLAIM = "INSURANCE_CLAIM"
    PURCHASE_ORDER = "PURCHASE_ORDER"


class PageSizeEnum(str, PyEnum):
    A4_PORTRAIT = "A4_PORTRAIT"
    A4_LANDSCAPE = "A4_LANDSCAPE"
    A5_PORTRAIT = "A5_PORTRAIT"
    THERMAL_80MM = "THERMAL_80MM"


class PrintModeEnum(str, PyEnum):
    DESIGNED = "DESIGNED"
    PREPRINTED_DATA_ONLY = "PREPRINTED_DATA_ONLY"


class ReportTemplate(AuditMixin, Base):
    """Stores visual drag-and-drop layout schemas and compiled Jinja2 HTML templates per tenant."""
    __tablename__ = "report_templates"
    __table_args__ = (
        Index("ix_report_templates_type_default", "report_type", "is_default"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_type: Mapped[ReportTypeEnum] = mapped_column(nullable=False, index=True)
    print_mode: Mapped[str] = mapped_column(
        String(32),
        default=PrintModeEnum.DESIGNED.value,
        server_default=text("'DESIGNED'"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_size: Mapped[PageSizeEnum] = mapped_column(default=PageSizeEnum.A4_PORTRAIT, nullable=False)
    paper_width_mm: Mapped[Optional[float]] = mapped_column(Float)
    paper_height_mm: Mapped[Optional[float]] = mapped_column(Float)
    page_margin_top_mm: Mapped[Optional[float]] = mapped_column(Float)
    page_margin_right_mm: Mapped[Optional[float]] = mapped_column(Float)
    page_margin_bottom_mm: Mapped[Optional[float]] = mapped_column(Float)
    page_margin_left_mm: Mapped[Optional[float]] = mapped_column(Float)
    content_offset_x_mm: Mapped[Optional[float]] = mapped_column(Float)
    content_offset_y_mm: Mapped[Optional[float]] = mapped_column(Float)

    # JSON string storing the visual drag-and-drop canvas blocks, (x,y) coordinates, and widget styles
    layout_schema: Mapped[str] = mapped_column(Text, nullable=False)

    # Compiled Jinja2 HTML and CSS executed by the sandboxed print engine
    html_template: Mapped[str] = mapped_column(Text, nullable=False)
    css_styles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    header_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    footer_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
