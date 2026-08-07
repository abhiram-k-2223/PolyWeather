"""Operations/admin API routes.

The subscription/payment stack has been removed from the roadmap, so the
ops router exposes only operational admin endpoints (feedback, config,
logs, health, analytics, training, source health, truth history).
"""

from fastapi import APIRouter, Request, Response

from web.services.ops_api import (
    get_ops_analytics_funnel,
    get_ops_config,
    get_ops_sensitive_config,
    get_ops_health_check,
    get_ops_logs,
    get_ops_observation_collector_status,
    get_ops_source_health,
    get_ops_truth_history,
    list_ops_feedback,
    update_ops_config,
    update_ops_feedback_status,
    get_ops_training_accuracy,
    update_ops_sensitive_config,
)
from web.services.request_timing import ServerTimingRecorder, attach_server_timing_header

router = APIRouter(tags=["ops"])


@router.get("/api/ops/online-users")
async def ops_online_users(request: Request, response: Response):
    timer = ServerTimingRecorder(
        request,
        log_name="ops_online_users_timing",
        prefix="ops_online_users",
        state_attr="ops_online_users_server_timing",
    )
    outcome = "ok"
    status_code = 200
    try:
        from src.utils.online_tracker import online_count
        return {"online": timer.measure("online_count", online_count)}
    except Exception:
        outcome = "exception"
        status_code = 500
        raise
    finally:
        timer.finish(outcome=outcome, status_code=status_code)
        attach_server_timing_header(
            response,
            request,
            "ops_online_users_server_timing",
        )


@router.get("/api/ops/feedback")
async def ops_feedback(request: Request, limit: int = 100, status: str = ""):
    return list_ops_feedback(request, limit=limit, status=status)


@router.post("/api/ops/feedback/{feedback_id}/status")
async def ops_feedback_update_status(request: Request, feedback_id: int):
    import json as _json
    body_bytes = await request.body()
    body = _json.loads(body_bytes.decode("utf-8") or "{}")
    status = str(body.get("status") or "").strip()
    return update_ops_feedback_status(request, feedback_id=feedback_id, status=status)


@router.get("/api/ops/analytics/funnel")
async def ops_analytics_funnel(request: Request, days: int = 30):
    return get_ops_analytics_funnel(request, days=days)


@router.get("/api/ops/truth-history")
async def ops_truth_history(
    request: Request,
    city: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
):
    return get_ops_truth_history(
        request,
        city=city,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


# ── Config ──────────────────────────────────────────────────────────

@router.get("/api/ops/config")
async def ops_config(request: Request):
    return get_ops_config(request)


@router.put("/api/ops/config")
async def ops_update_config(request: Request):
    import json as _json
    body_bytes = await request.body()
    body = _json.loads(body_bytes.decode("utf-8"))
    key = str(body.get("key") or "").strip()
    value = str(body.get("value") or "")
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="key is required")
    return update_ops_config(request, key, value)


@router.get("/api/ops/sensitive-config")
async def ops_sensitive_config(request: Request):
    return get_ops_sensitive_config(request)


@router.put("/api/ops/sensitive-config")
async def ops_update_sensitive_config(request: Request):
    import json as _json
    body_bytes = await request.body()
    body = _json.loads(body_bytes.decode("utf-8"))
    key = str(body.get("key") or "").strip()
    value = str(body.get("value") or "")
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="key is required")
    return update_ops_sensitive_config(request, key, value)


# ── Logs ────────────────────────────────────────────────────────────

@router.get("/api/ops/logs")
async def ops_logs(
    request: Request,
    level: str = "",
    lines: int = 100,
):
    return get_ops_logs(request, level=level, lines=lines)


@router.get("/api/ops/health-check")
async def ops_health_check(request: Request):
    return get_ops_health_check(request)


@router.get("/api/ops/source-health")
async def ops_source_health(
    request: Request,
    cities: str = "",
    limit: int = 80,
):
    return get_ops_source_health(request, cities=cities, limit=limit)


@router.get("/api/ops/observation-collector-status")
async def ops_observation_collector_status(
    request: Request,
    limit: int = 200,
):
    return get_ops_observation_collector_status(request, limit=limit)


@router.get("/api/ops/training/accuracy")
async def ops_training_accuracy(request: Request):
    return get_ops_training_accuracy(request)