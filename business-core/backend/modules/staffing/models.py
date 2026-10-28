from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    literal_column,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from backend.db.base import Base


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("employee_id", "cafe_id", name="uq_employees_employee_cafe"),
        Index("ix_employees_cafe_id_active", "cafe_id", "active"),
    )

    employee_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cafe: Mapped["Cafe"] = relationship()
    shifts: Mapped[list["Shift"]] = relationship(
        back_populates="employee",
        primaryjoin=lambda: Employee.employee_id == foreign(Shift.employee_id),
        foreign_keys=lambda: [Shift.employee_id],
    )


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("start_at < end_at", name="ck_shifts_start_before_end"),
        CheckConstraint(
            "status IN ('scheduled', 'cancelled')",
            name="ck_shifts_status",
        ),
        ForeignKeyConstraint(
            ["employee_id", "cafe_id"],
            ["employees.employee_id", "employees.cafe_id"],
            name="fk_shifts_employee_cafe",
        ),
        ExcludeConstraint(
            ("employee_id", "="),
            (literal_column("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=text("status = 'scheduled'"),
            using="gist",
            name="excl_shifts_employee_scheduled_overlap",
        ),
        Index("ix_shifts_cafe_id_start_at", "cafe_id", "start_at"),
    )

    shift_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cafe: Mapped["Cafe"] = relationship()
    employee: Mapped["Employee"] = relationship(
        back_populates="shifts",
        primaryjoin=lambda: foreign(Shift.employee_id) == Employee.employee_id,
        foreign_keys=lambda: [Shift.employee_id],
    )
