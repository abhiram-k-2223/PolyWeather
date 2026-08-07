"""Authentication API service functions.

The subscription/payment stack has been removed from the roadmap, so the
auth /me payload is identity-only: it reports whether a Supabase identity
is bound and otherwise exposes no entitlement/pricing fields.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, TypeVar

from fastapi import HTTPException, Request
from loguru import logger

import web.routes as legacy_routes


T = TypeVar("T")


class _AuthMeTimer:
    def __init__(self, request: Request):
        self.request = request
        self.started = time.perf_counter()
        self.timings_ms: Dict[str, float] = {}

    def measure(self, stage: str, action: Callable[[], T]) -> T:
        started = time.perf_counter()
        try:
            return action()
        finally:
            self.timings_ms[stage] = round(
                (time.perf_counter() - started) * 1000.0,
                1,
            )

    def finish(
        self,
        *,
        authenticated: Optional[bool],
        outcome: str,
        status_code: int,
    ) -> None:
        self.timings_ms["total"] = round(
            (time.perf_counter() - self.started) * 1000.0,
            1,
        )
        server_timing = ", ".join(
            f"backend_{stage};dur={max(0.0, duration):.1f}"
            for stage, duration in self.timings_ms.items()
        )
        self.request.state.auth_me_server_timing = server_timing
        logger.info(
            "auth_me_timing outcome={} status_code={} authenticated={} timings_ms={}",
            outcome,
            status_code,
            authenticated,
            self.timings_ms,
        )


def get_auth_me_payload(request: Request) -> Dict[str, Any]:
    timer = _AuthMeTimer(request)
    authenticated_for_log: Optional[bool] = None
    outcome = "ok"
    status_code = 200

    try:
        timer.measure("bind_identity", lambda: legacy_routes._assert_entitlement(request))
        user_id = str(getattr(request.state, "auth_user_id", "") or "").strip()
        email = str(getattr(request.state, "auth_email", "") or "").strip() or None
        authenticated_for_log = bool(user_id)

        payload = {
            "authenticated": bool(user_id),
            "user_id": user_id or None,
            "email": email,
        }
        return payload
    except HTTPException as exc:
        outcome = f"http_{exc.status_code}"
        status_code = exc.status_code
        raise
    except Exception:
        outcome = "exception"
        status_code = 500
        raise
    finally:
        timer.finish(
            authenticated=authenticated_for_log,
            outcome=outcome,
            status_code=status_code,
        )