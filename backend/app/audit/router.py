"""Expose a read-only, permission-gated audit history and CSV export."""

import csv
import io
from datetime import datetime, timezone
from typing import Annotated
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pymongo.asynchronous.database import AsyncDatabase
from app.admin.repository import AdminRepository
from app.admin.service import authorize
from app.auth.router import unrestricted_user
from app.core.database import get_db

router = APIRouter(prefix='/api/audit-logs', tags=['Audit'])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


def present(doc: dict) -> dict:
    """Convert database identifiers to public strings."""
    timestamp = doc['timestamp']
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {'id': str(doc['_id']), 'timestamp': timestamp, 'userId': str(doc['userId']) if doc.get('userId') else None, 'userName': doc.get('userName', ''), 'action': doc.get('action', ''), 'entityType': doc.get('entityType', ''), 'entityId': doc.get('entityId'), 'details': doc.get('details', '')}


def safe_csv(value: object) -> str:
    """Prevent spreadsheet formulas in text cells."""
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


@router.get('', response_model=None)
async def listing(db: Db, user: User, userId: str | None = Query(None, max_length=24), action: str | None = Query(None, max_length=50), start: datetime | None = Query(None, alias='from'), end: datetime | None = Query(None, alias='to'), format: str = Query('json', pattern='^(json|csv)$'), limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict | Response:
    """Return a filtered page or export the same bounded page as CSV."""
    repo = AdminRepository(db)
    await authorize(repo, user, 'view', {'Administrator', 'Auditor'})
    if format == 'csv':
        await authorize(repo, user, 'export', {'Administrator', 'Auditor'})
    query: dict = {}
    if userId:
        if not ObjectId.is_valid(userId):
            raise HTTPException(422, 'Invalid user ID')
        query['userId'] = ObjectId(userId)
    if action:
        query['action'] = action
    if start or end:
        if start and start.tzinfo is None or end and end.tzinfo is None:
            raise HTTPException(422, 'Date filters must include a timezone')
        if start and end and start > end:
            raise HTTPException(422, 'Invalid date range')
        query['timestamp'] = {}
        if start:
            query['timestamp']['$gte'] = start.astimezone(timezone.utc)
        if end:
            query['timestamp']['$lte'] = end.astimezone(timezone.utc)
    page = await repo.page('audit_logs', query, limit, offset)
    items = [present(doc) for doc in page['items']]
    if format == 'json':
        return {'items': items, 'total': page['total'], 'canExport': 'export' in await repo.permissions(user['role'])}
    output = io.StringIO(newline='')
    fields = ['timestamp', 'userId', 'userName', 'action', 'entityType', 'entityId', 'details']
    writer = csv.writer(output)
    writer.writerow(fields)
    for item in items:
        writer.writerow([safe_csv(item[field]) for field in fields])
    await repo.audit(user, 'export', 'audit', None, 'Audit log page exported')
    return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="audit-logs.csv"'})
