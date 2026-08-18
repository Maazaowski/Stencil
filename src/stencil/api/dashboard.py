"""Dashboard analytics API endpoints."""

from fastapi import APIRouter, Query

from stencil.api.deps import DbSession
from stencil.api.schemas import (
    CostByModelPoint,
    CostBySupplierPoint,
    CostDataPoint,
    DashboardStats,
    MonthlyCostPoint,
    MonthlyStats,
    VolumeDataPoint,
)
from stencil.db import crud

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_MONTH_PATTERN = r"^\d{4}-\d{2}$"


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: DbSession):
    return crud.get_dashboard_stats(db)


@router.get("/volume", response_model=list[VolumeDataPoint])
def get_volume(db: DbSession, days: int = Query(30, ge=1, le=365)):
    return crud.get_volume_over_time(db, days=days)


@router.get("/costs", response_model=list[CostDataPoint])
def get_costs(db: DbSession, days: int = Query(30, ge=1, le=365)):
    return crud.get_cost_over_time(db, days=days)


@router.get("/ai-calls")
def get_ai_call_breakdown(db: DbSession, days: int = Query(30, ge=1, le=365)):
    return crud.get_ai_cost_summary(db, days=days)


@router.get("/ai-cost-breakdown")
def get_ai_cost_breakdown(db: DbSession, days: int = Query(30, ge=1, le=365)):
    """AI cost by purpose: production extraction vs classification vs model training."""
    return crud.get_ai_cost_breakdown(db, days=days)


@router.get("/monthly", response_model=MonthlyStats)
def get_monthly(db: DbSession, month: str = Query(..., pattern=_MONTH_PATTERN)):
    """Single-month cost + volume rollup for the given ``YYYY-MM`` month."""
    return crud.get_monthly_stats(db, month)


@router.get("/cost-by-month", response_model=list[MonthlyCostPoint])
def get_cost_by_month(db: DbSession, months: int = Query(12, ge=1, le=24)):
    """AI cost per calendar month for the last N months (continuous, oldest first)."""
    return crud.get_cost_by_month(db, months=months)


@router.get("/cost-by-model", response_model=list[CostByModelPoint])
def get_cost_by_model(db: DbSession, month: str = Query(..., pattern=_MONTH_PATTERN)):
    """Per-AI-model cost for a month, highest cost first."""
    return crud.get_cost_by_model(db, month)


@router.get("/cost-by-supplier", response_model=list[CostBySupplierPoint])
def get_cost_by_supplier(db: DbSession, month: str = Query(..., pattern=_MONTH_PATTERN)):
    """Per-supplier/account cost for a month, highest cost first."""
    return crud.get_cost_by_supplier(db, month)
