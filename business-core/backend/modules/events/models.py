"""Café event calendar: past events (to learn their effect on sales) and upcoming ones
(what Muse "detects"). Comparable past days are found by ``event_type``."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

EVENT_TYPES = (
    "farmers_market",
    "holiday",
    "community_festival",
    "sports",
    "school_break",
    "weather",
    "other",
)
EVENT_STATUSES = ("upcoming", "happened", "cancelled")
RECURRENCES = ("one_off", "weekly", "monthly", "yearly")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class CafeEvent(Base):
    __tablename__ = "cafe_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", EVENT_TYPES), name="ck_cafe_events_type"),
        CheckConstraint(_in("status", EVENT_STATUSES), name="ck_cafe_events_status"),
        CheckConstraint(f"recurrence IS NULL OR {_in('recurrence', RECURRENCES)}", name="ck_cafe_events_recurrence"),
        CheckConstraint("ends_at > starts_at", name="ck_cafe_events_window"),
        CheckConstraint("distance_m IS NULL OR distance_m >= 0", name="ck_cafe_events_distance"),
        CheckConstraint(
            "expected_attendance IS NULL OR expected_attendance >= 0", name="ck_cafe_events_attendance"
        ),
        Index("ix_cafe_events_cafe_type_start", "cafe_id", "event_type", "starts_at"),
        Index("ix_cafe_events_cafe_status_start", "cafe_id", "status", "starts_at"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attendance_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
