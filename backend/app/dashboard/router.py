"""Expose the authorized executive dashboard read model."""

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from app.auth.router import unrestricted_user
from app.core.database import get_db
from app.dashboard.service import executive

router = APIRouter(prefix='/api/dashboard', tags=['Dashboard'])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


class PortfolioMetric(BaseModel):
    """Describe the selected application metrics for one portfolio."""
    id: str
    name: str
    applicationCount: int
    qualityScore: int | None
    applicationsAtRisk: int


class PortfolioMetricsPage(BaseModel):
    """Expose a bounded portion of the portfolio breakdown."""
    items: list[PortfolioMetric]
    total: int


class ExecutiveDashboard(BaseModel):
    """Expose complete scalar totals and a paginated breakdown."""
    enterpriseQualityScore: int | None
    applicationHealthScore: int | None
    automationMaturityScore: int | None
    openCriticalDefects: int
    applicationsAtRisk: int
    totalApplications: int
    totalPortfolios: int
    portfolios: PortfolioMetricsPage


@router.get('/executive', response_model=ExecutiveDashboard)
async def get_executive(db: Db, user: User, portfolioId: str | None = Query(None, min_length=24, max_length=24, pattern=r'^[0-9a-fA-F]{24}$'), productArea: str = Query('', max_length=200), riskLevel: Literal['Low', 'Medium', 'High'] | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """Return metrics over permitted portfolios and matching applications."""
    return await executive(db, user, portfolioId.lower() if portfolioId else None, productArea, riskLevel, limit, offset)
