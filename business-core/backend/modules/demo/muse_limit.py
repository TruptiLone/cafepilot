"""A daily limit on Muse calls for the public demo (MUSE_DAILY_LIMIT; unset means no limit).

Every model request through call_model / call_image counts as one call. The day is the café's day
(Pacific time), so the allowance resets at midnight there. Routes that start Muse work check the
limit first (create_planner_client / create_chat_client) so a visitor gets a friendly 429 instead of
a half-finished plan. The count lives in memory: a server restart starts the day's count again.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.db.config import assistant_name, get_settings

CAFE_TIME = ZoneInfo("America/Los_Angeles")
LIMIT_MESSAGE = (
    "The demo has used today's {assistant} allowance. It resets at midnight Pacific time. "
    "Until then you can still look around: plans, approvals, stock and deliveries already made all work."
)


class MuseLimitReached(RuntimeError):
    def __init__(self) -> None:
        super().__init__(LIMIT_MESSAGE.format(assistant=assistant_name()))


_lock = threading.Lock()
_day: date | None = None
_used = 0


def _today(now: datetime | None = None) -> date:
    return (now or datetime.now(CAFE_TIME)).astimezone(CAFE_TIME).date()


def daily_limit() -> int | None:
    limit = get_settings().muse_daily_limit
    return limit if limit is not None and limit >= 0 else None


def _roll(now: datetime | None) -> None:
    global _day, _used
    today = _today(now)
    if _day != today:
        _day, _used = today, 0


def check(now: datetime | None = None) -> None:
    """Raise MuseLimitReached when today's allowance is used up."""
    limit = daily_limit()
    if limit is None:
        return
    with _lock:
        _roll(now)
        if _used >= limit:
            raise MuseLimitReached()


def take(now: datetime | None = None) -> None:
    """Count one Muse call; raise MuseLimitReached (without counting) when none are left."""
    global _used
    limit = daily_limit()
    with _lock:
        _roll(now)
        if limit is not None and _used >= limit:
            raise MuseLimitReached()
        _used += 1


def usage(now: datetime | None = None) -> dict[str, int | str | None]:
    limit = daily_limit()
    with _lock:
        _roll(now)
        used = _used
    midnight = datetime.combine(_today(now) + timedelta(days=1), datetime.min.time(), CAFE_TIME)
    return {"used": used, "limit": limit, "left": None if limit is None else max(limit - used, 0),
            "resets_at": midnight.isoformat()}


def reset_for_tests() -> None:
    global _day, _used
    with _lock:
        _day, _used = None, 0
