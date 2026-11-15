"""/api/v1/demo: the access code, today's Muse allowance and "Start over"."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.db import visitors as visitor_dbs
from backend.db.session import get_db
from backend.modules.demo import access, muse_limit

router = APIRouter(tags=["demo"])
_reset_lock = threading.Lock()


class AccessOut(BaseModel):
    required: bool = Field(description="True when the site asks for an access code.")
    granted: bool
    muse_calls_left: int | None = Field(description="Muse calls left today; None when there is no limit.")
    model_provider: str = Field(description='Which models run the café: "meta" (Muse) or "openai"; the screens name them.')


class AccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=200)


def _ensure_cafe(request: Request, response: Response) -> None:
    """With a copy per visitor: make this visitor's café if they don't have a live one yet."""
    if not visitor_dbs.enabled():
        return
    visitor_id = request.cookies.get(access.VISITOR_COOKIE)
    if visitor_dbs.visitors.has(visitor_id):
        return
    visitor_id = visitor_dbs.visitors.create(visitor_id)  # reuses a valid old id, so the cookie stays the same
    response.set_cookie(access.VISITOR_COOKIE, visitor_id, max_age=access.MAX_AGE_SECONDS, httponly=True,
                        samesite="lax", secure=get_settings().public_demo, path="/")


def _status(request: Request, response: Response, granted: bool | None = None) -> AccessOut:
    granted = access.has_access(request) if granted is None else granted
    if granted:
        _ensure_cafe(request, response)
    return AccessOut(required=access.access_code() is not None, granted=granted,
                     muse_calls_left=muse_limit.usage()["left"],  # type: ignore[arg-type]
                     model_provider=get_settings().model_provider)


@router.get("/access", response_model=AccessOut)
def access_status(request: Request, response: Response) -> AccessOut:
    """Whether the visitor may use the demo; once they may, their own café copy is made here (first visit)."""
    return _status(request, response)


@router.post("/access", response_model=AccessOut)
def enter_code(body: AccessIn, request: Request, response: Response) -> AccessOut:
    code = access.access_code()
    if code is None:
        return _status(request, response, granted=True)
    address = access.client_address(request)
    if access.failures.blocked(address):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many wrong codes. Wait 15 minutes, then try again.")
    if not access.code_matches(body.code, code):
        access.failures.failed(address)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="That code isn't right. Check it and try again.")
    response.set_cookie(
        access.COOKIE, access.make_token(code), max_age=access.MAX_AGE_SECONDS, httponly=True, samesite="lax",
        secure=get_settings().public_demo, path="/",
    )
    return _status(request, response, granted=True)


class ResetOut(BaseModel):
    reset: bool
    message: str


@router.post("/reset", response_model=ResetOut)
def start_over(request: Request, database_session: Session = Depends(get_db)) -> ResetOut:
    """Put the demo café back to the start: plans, orders, shifts, delivery checks and credit requests.
    With a copy per visitor, only this visitor's copy is replaced with a fresh one from the template."""
    from scripts.seed_event_surge import CAFE_ID, apply_reset, hide_demo_discovery, plan_reset, seed_event_surge

    if not _reset_lock.acquire(blocking=False):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The demo is already starting over. Try again in a moment.")
    try:
        if visitor_dbs.enabled():
            database_session.close()
            visitor_dbs.visitors.recreate(request.cookies[access.VISITOR_COOKIE])  # the gate checked it exists
            return ResetOut(reset=True, message="Your demo café is back to Thursday, Nov 5, 6 PM.")
        plan = plan_reset(database_session)
        if not plan.empty:
            apply_reset(database_session, plan)
        seed_event_surge(database_session)
        hide_demo_discovery(database_session, CAFE_ID)
        database_session.commit()
    except Exception:
        database_session.rollback()
        raise
    finally:
        _reset_lock.release()
    return ResetOut(reset=True, message="The demo café is back to Thursday, Nov 5, 6 PM.")
