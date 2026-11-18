"""Expose bounded inventory reports and scoped CSV downloads."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.router import unrestricted_user
from app.core.database import get_db
from app.reports import service

router = APIRouter(prefix='/api/reports', tags=['Reports'])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


@router.get('/applications', response_model=None)
async def application_report(db: Db, user: User, search: str = Query('', max_length=200), portfolioId: str | None = Query(None, min_length=24, max_length=24, pattern=r'^[0-9a-fA-F]{24}$'), criticality: Literal['Low', 'Medium', 'High', 'Critical'] | None = None, riskLevel: Literal['Low', 'Medium', 'High'] | None = None, lifecycleStatus: Literal['Active', 'Deprecated', 'Retired'] | None = None, format: Literal['json', 'csv'] = 'json', limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict | Response:
    """List matching applications or export all visible matches."""
    async def page(size: int, start: int) -> dict:
        """Apply the validated filters to a scoped page."""
        return await service.applications(db, user, search, portfolioId.lower() if portfolioId else None, criticality, riskLevel, lifecycleStatus, size, start)
    if format == 'json':
        return await page(limit, offset)
    content = await service.export_csv(db, user, 'applications', page)
    return Response(content, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="applications.csv"'})


@router.get('/portfolios', response_model=None)
async def portfolio_report(db: Db, user: User, search: str = Query('', max_length=200), status: Literal['Active', 'Archived'] | None = None, productArea: str = Query('', max_length=200), format: Literal['json', 'csv'] = 'json', limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict | Response:
    """List matching portfolios or export all visible matches."""
    async def page(size: int, start: int) -> dict:
        """Apply the validated filters to a scoped page."""
        return await service.portfolios(db, user, search, status, productArea, size, start)
    if format == 'json':
        return await page(limit, offset)
    content = await service.export_csv(db, user, 'portfolios', page)
    return Response(content, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="portfolios.csv"'})
