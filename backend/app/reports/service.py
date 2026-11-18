"""Build scoped inventory reports and audited spreadsheet-safe exports."""

import csv
import io
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from pymongo.asynchronous.database import AsyncDatabase

from app.admin.repository import AdminRepository
from app.applications.repository import ApplicationRepository
from app.applications.service import listing
from app.audit.router import safe_csv
from app.portfolios.repository import PortfolioRepository
from app.portfolios.service import list_portfolios

EXPORT_ROLES = {'Administrator', 'Executive', 'Portfolio Leader', 'Engineer', 'Auditor'}
APPLICATION_FIELDS = ['name', 'portfolioName', 'criticality', 'tier', 'lifecycleStatus', 'applicationHealthScore', 'qualityScore', 'automationCoverage', 'openCriticalDefects', 'riskLevel']
PORTFOLIO_FIELDS = ['name', 'owner', 'productArea', 'applicationCount', 'qualityScore', 'status']


async def can_export(db: AsyncDatabase, user: dict) -> bool:
    """Intersect the live export grant with fixed role restrictions."""
    return user['role'] in EXPORT_ROLES and 'export' in await AdminRepository(db).permissions(user['role'])


async def applications(db: AsyncDatabase, user: dict, search: str, portfolio_id: str | None, criticality: str | None, risk: str | None, lifecycle: str | None, limit: int, offset: int) -> dict:
    """Return a scoped application page and current export capability."""
    page = await listing(ApplicationRepository(db), user, search, portfolio_id, criticality, risk, lifecycle, limit, offset)
    return {**page, 'canExport': await can_export(db, user)}


async def portfolios(db: AsyncDatabase, user: dict, search: str, status: str | None, product_area: str, limit: int, offset: int) -> dict:
    """Return a scoped portfolio page and current export capability."""
    page = await list_portfolios(PortfolioRepository(db), user, search, status, product_area, limit, offset)
    return {**page, 'canExport': await can_export(db, user)}


async def export_csv(db: AsyncDatabase, user: dict, kind: str, page_fn: Callable[[int, int], Awaitable[dict]]) -> str:
    """Export all matching pages and record success before returning the CSV."""
    if not await can_export(db, user):
        raise HTTPException(403, 'Export is not permitted')
    fields = APPLICATION_FIELDS if kind == 'applications' else PORTFOLIO_FIELDS
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(fields)
    offset = 0
    while True:
        page = await page_fn(100, offset)
        for item in page['items']:
            writer.writerow([safe_csv(item.get(field)) for field in fields])
        offset += len(page['items'])
        if not page['items'] and offset < page['total']:
            raise HTTPException(409, 'Report changed during export; retry')
        if offset >= page['total']:
            break
    if not await can_export(db, user):
        raise HTTPException(403, 'Export is not permitted')
    await AdminRepository(db).audit(user, 'export', kind[:-1], None, f'{kind.title()} report exported ({offset} rows)')
    return output.getvalue()
