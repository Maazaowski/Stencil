"""Pipeline control API endpoints."""

import asyncio
import json
import math

import structlog
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from stencil.api.deps import DbSession, Pagination
from stencil.api.schemas import (
    ExtractionJobResponse,
    PaginatedResponse,
    ProcessingLogResponse,
)
from stencil.config import settings
from stencil.db import crud

logger = structlog.get_logger()

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/jobs", response_model=PaginatedResponse[ExtractionJobResponse])
def list_jobs(
    db: DbSession,
    pagination: Pagination,
    status: str | None = Query(None),
    extraction_path: str | None = Query(None),
):
    items, total = crud.list_extraction_jobs(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        status=status,
        extraction_path=extraction_path,
    )
    return PaginatedResponse(
        items=[ExtractionJobResponse.model_validate(j) for j in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=max(1, math.ceil(total / pagination.per_page)),
    )


@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_job(db: DbSession, job_id: str):
    job = crud.get_extraction_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return ExtractionJobResponse.model_validate(job)


@router.get("/logs", response_model=PaginatedResponse[ProcessingLogResponse])
def list_logs(
    db: DbSession,
    pagination: Pagination,
    intake_id: str | None = Query(None),
    step: str | None = Query(None),
    status: str | None = Query(None),
):
    items, total = crud.list_processing_logs(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        intake_id=intake_id,
        step=step,
        status=status,
    )
    return PaginatedResponse(
        items=[ProcessingLogResponse.model_validate(log) for log in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=max(1, math.ceil(total / pagination.per_page)),
    )


def _websocket_session_ok(websocket: WebSocket) -> bool:
    """Validate the session cookie on a WebSocket upgrade.

    HTTP middleware does not run for WebSocket scopes, so this endpoint has to
    do its own auth. Mirrors the HTTP gate: allowed while the users table is
    still empty (fresh install), required once any user exists.
    """
    from stencil import auth as _auth
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        if _auth.validate_session(db, websocket.cookies.get(_auth.SESSION_COOKIE)) is not None:
            return True
        return _auth.user_count(db) == 0
    except Exception:  # pragma: no cover - DB down: fail closed
        return False
    finally:
        db.close()


@router.websocket("/live/{intake_id}")
async def pipeline_live(websocket: WebSocket, intake_id: str):
    """WebSocket endpoint for live pipeline progress events.

    The frontend connects after uploading a PDF to watch extraction progress
    in real time. Events are published by the pipeline processor via Redis pub/sub.
    """
    if not await run_in_threadpool(_websocket_session_ok, websocket):
        # Close before accepting so an unauthenticated peer never sees events.
        await websocket.close(code=1008, reason="Not authenticated")
        logger.warning("ws.rejected_unauthenticated", intake_id=intake_id)
        return

    await websocket.accept()

    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        channel = f"pipeline:{intake_id}"
        await pubsub.subscribe(channel)

        logger.info("ws.connected", intake_id=intake_id)

        # Send any existing logs for this intake (for reconnection scenarios)
        # This is non-blocking — if no DB session is available, skip
        try:
            from stencil.db.session import SessionLocal
            with SessionLocal() as db:
                logs = crud.get_processing_logs(db, intake_id)
                for log in logs:
                    await websocket.send_json({
                        "type": "pipeline_event",
                        "intake_id": intake_id,
                        "step": log.step,
                        "status": log.status,
                        "message": log.message or "",
                        "details": log.details or {},
                        "timestamp": log.timestamp.isoformat() if log.timestamp else "",
                    })
        except Exception:
            pass  # Non-critical — live events will still work

        # Listen for new events
        while True:
            message = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=120,  # 2 min max connection
            )
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)

                # Close on terminal events
                if data.get("step") == "pipeline" and data.get("status") in ("completed", "failed"):
                    break

    except WebSocketDisconnect:
        logger.info("ws.disconnected", intake_id=intake_id)
    except TimeoutError:
        logger.info("ws.timeout", intake_id=intake_id)
        await websocket.close(code=1000, reason="Timeout")
    except Exception as e:
        logger.warning("ws.error", intake_id=intake_id, error=str(e))
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await r.close()
        except Exception:
            pass
