"""Every model call goes through call_model() (call_image() for Muse Image); record() keeps one row per
request in ai_calls.

A row is what the owner would count as one request: a plan that needed a second try is one row with
attempts 2 and outcome "retried". Rows keep a short request summary only (no prompts, nothing that
looks like a key) and are saved in their own session, so a request that fails or rolls back is still
recorded. The jsonl log (planner._log_call) is unchanged.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.modules.agent.models import AICall

# Tests point this at a rolled-back connection.
SESSION_FACTORY = SessionLocal

SUMMARY_LIMIT = 120
ERROR_LIMIT = 200
_KEYLIKE = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}|\bBearer\s+\S+|\b[A-Za-z0-9_\-]{32,}\b", re.IGNORECASE)
# po_agent_b0dc…, EVT_SC_FARMERS_MARKET_20261108, RUN-901A7DA9234D, DEMO_EMP_004 (PO-1042 stays: owners see it)
_INTERNAL_ID = re.compile(r"\b(?:[A-Za-z]+_)+[A-Za-z0-9_]*\d\w*\b|\b(?:RUN|AIC)-[0-9A-F]{6,}\b")


def call_model(client: Any, *, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> tuple[Any, int]:
    """The one way to call a model: returns (response, latency_ms). A failure carries latency_ms too."""
    started = time.monotonic()
    try:
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    except Exception as error:
        error.latency_ms = int((time.monotonic() - started) * 1000)  # type: ignore[attr-defined]
        raise
    return response, int((time.monotonic() - started) * 1000)


def call_image(client: Any, *, model: str, prompt: str, **kwargs: Any) -> tuple[Any, int]:
    """call_model for an image model (Muse Image): returns (response, latency_ms); a failure carries latency_ms."""
    started = time.monotonic()
    try:
        response = client.images.generate(model=model, prompt=prompt, **kwargs)
    except Exception as error:
        error.latency_ms = int((time.monotonic() - started) * 1000)  # type: ignore[attr-defined]
        raise
    return response, int((time.monotonic() - started) * 1000)


def is_timeout(error: BaseException) -> bool:
    return "Timeout" in type(error).__name__ or bool(getattr(error, "timeout", False))


def summarize(text: Any, limit: int = SUMMARY_LIMIT) -> str:
    """One short line: whitespace collapsed, anything key-like hidden, cut to the limit."""
    line = _KEYLIKE.sub("[hidden]", " ".join(str(text or "").split()))
    line = " ".join(_INTERNAL_ID.sub("", line).split())  # internal IDs mean nothing to the owner
    return line if len(line) <= limit else line[: limit - 1] + "…"


def record(
    *,
    cafe_id: str | None,
    purpose: str,
    model: str,
    latency_ms: int,
    outcome: str,
    request: str,
    attempts: int = 1,
    run_id: str | None = None,
    error: str | None = None,
) -> str | None:
    """Save one row; returns its id, or None if it could not be saved (recording never breaks a request)."""
    call_id = f"AIC-{uuid4().hex[:12].upper()}"
    try:
        with SESSION_FACTORY() as session:
            session.add(AICall(
                id=call_id, cafe_id=cafe_id, created_at=datetime.now(timezone.utc), purpose=purpose, model=model,
                latency_ms=max(int(latency_ms), 0), attempts=max(int(attempts), 1), outcome=outcome, run_id=run_id,
                request_summary=summarize(request), error=summarize(error, ERROR_LIMIT) if error else None,
            ))
            session.commit()
    except Exception:  # pragma: no cover - the log must never break planning or chat
        return None
    return call_id


def link_run(call_id: str | None, run_id: str) -> None:
    """Attach the run a plan became (it only exists once planning has finished)."""
    if not call_id:
        return
    try:
        with SESSION_FACTORY() as session:
            session.execute(update(AICall).where(AICall.id == call_id).values(run_id=run_id))
            session.commit()
    except Exception:  # pragma: no cover
        pass


def recent(session: Session, cafe_id: str | None, limit: int) -> list[AICall]:
    query = select(AICall).order_by(AICall.created_at.desc()).limit(limit)
    if cafe_id:
        query = query.where(AICall.cafe_id == cafe_id)
    return list(session.scalars(query))
