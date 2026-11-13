"""Access code for the public demo.

When DEMO_ACCESS_CODE is set, every /api call needs the access cookie. A visitor gets it by sending
the code to POST /api/v1/demo/access. The cookie holds an expiry and an HMAC of it, keyed by the
code, so it can't be forged and changing the code signs everyone out. With no code set (local
development, tests) nothing is checked.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.db.config import get_settings

COOKIE = "cafepilot_access"
MAX_AGE_SECONDS = 7 * 24 * 3600
OPEN_PATHS = ("/api/v1/demo/access",)  # the gate itself
LOCKED_HEADER = "X-CafePilot-Access"

# Wrong codes: at most this many per address in the window, then a pause.
MAX_FAILURES = 8
FAILURE_WINDOW_SECONDS = 15 * 60


def access_code() -> str | None:
    code = get_settings().demo_access_code
    value = code.get_secret_value().strip() if code else ""
    return value or None


def _sign(code: str, expires: int) -> str:
    key = hashlib.sha256(f"cafepilot-access:{code}".encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_token(code: str, now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + MAX_AGE_SECONDS)
    return f"v1.{expires}.{_sign(code, expires)}"


def token_is_valid(token: str | None, code: str, now: float | None = None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdigit():
        return False
    expires = int(parts[1])
    if expires < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(parts[2], _sign(code, expires))


def code_matches(given: str, code: str) -> bool:
    return hmac.compare_digest(given.strip().encode(), code.encode())


def has_access(request: Request) -> bool:
    code = access_code()
    return code is None or token_is_valid(request.cookies.get(COOKIE), code)


def client_address(request: Request) -> str:
    # Behind the host's proxy the visitor's address is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class FailureLimiter:
    """Counts wrong codes per address in a sliding window (in memory; a restart clears it)."""

    def __init__(self, limit: int = MAX_FAILURES, window: float = FAILURE_WINDOW_SECONDS) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, address: str, now: float) -> deque[float]:
        times = self._failures.setdefault(address, deque())
        while times and now - times[0] > self.window:
            times.popleft()
        return times

    def blocked(self, address: str, now: float | None = None) -> bool:
        with self._lock:
            return len(self._recent(address, now if now is not None else time.monotonic())) >= self.limit

    def failed(self, address: str, now: float | None = None) -> None:
        with self._lock:
            self._recent(address, now if now is not None else time.monotonic()).append(
                now if now is not None else time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


failures = FailureLimiter()


VISITOR_COOKIE = "cafepilot_visitor"


def _locked(detail: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail}, headers={LOCKED_HEADER: "required"})


class DemoGate:
    """ASGI middleware for /api calls: the access code first, then the visitor's own café copy.

    Pure ASGI (not BaseHTTPMiddleware) so the visitor's database, kept in a context variable, reaches the
    endpoint and the threads FastAPI runs it in.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        from backend.db.visitors import current_database, database_name, enabled, visitors

        request = Request(scope)
        gate_itself = scope["path"] in OPEN_PATHS
        if not gate_itself and not has_access(request):
            await _locked("Enter the access code to use the CaféPilot demo.")(scope, receive, send)
            return
        name = None
        if enabled():
            visitor_id = request.cookies.get(VISITOR_COOKIE)
            if visitors.has(visitor_id):
                visitors.touch(visitor_id)  # type: ignore[arg-type]
                name = database_name(visitor_id)  # type: ignore[arg-type]
            elif not gate_itself:
                # No café yet, or it was closed after sitting unused: the page asks the gate for a fresh one.
                await _locked("Your demo café was closed after a while without use. Opening a fresh one…")(
                    scope, receive, send)
                return
        token = current_database.set(name)
        try:
            await self.app(scope, receive, send)
        finally:
            current_database.reset(token)
