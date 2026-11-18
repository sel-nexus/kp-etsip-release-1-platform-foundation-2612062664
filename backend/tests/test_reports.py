"""Exercise reports through HTTP against disposable MongoDB databases."""

import csv
import io
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo import AsyncMongoClient, MongoClient

from app.core.config import Settings
from app.main import create_app
from app.reports import service


@pytest.fixture
def client():
    """Start an isolated seeded API and discard its test database."""
    name = 'ketsip_reports_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri=os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def login(http, role='leader'):
    """Authenticate one of the seeded demo users."""
    response = http.post('/api/auth/login', json={'email': f'{role}@ketsip.local', 'password': 'Initial-Demo-Password123!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_json_reports_scope_filters_and_pagination(client):
    """Apply assignment, literal search, filters and pagination to both reports."""
    http, db = client
    headers = login(http)
    payments = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    other = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    result = http.get('/api/reports/applications?limit=1&offset=1', headers=headers)
    assert result.status_code == 200 and result.json()['total'] == 3
    assert len(result.json()['items']) == 1 and result.json()['canExport'] is True
    response = http.get(f'/api/reports/applications?portfolioId={other}', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    response = http.get(f'/api/reports/applications?portfolioId={payments}&riskLevel=High', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 1
    response = http.get('/api/reports/applications?search=.*', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    response = http.get('/api/reports/portfolios?search=Payments', headers=headers)
    assert response.status_code == 200
    report = response.json()
    assert report['total'] == 1 and report['items'][0]['applicationCount'] == 3
    response = http.get('/api/reports/portfolios?search=Customer', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    response = http.get('/api/reports/portfolios', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    response = http.get('/api/reports/applications', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0


def test_export_permissions_are_live_and_fixed(client):
    """Deny read-only exports even if granted and honor revocation immediately."""
    http, db = client
    leader = login(http)
    readonly = login(http, 'readonly')
    assert http.get('/api/reports/applications').status_code == 401
    db.roles.update_one({'name': 'Read Only'}, {'$addToSet': {'permissions': 'export'}})
    for kind in ('applications', 'portfolios'):
        assert http.get(f'/api/reports/{kind}?format=csv', headers=readonly).status_code == 403
        response = http.get(f'/api/reports/{kind}', headers=readonly)
        assert response.status_code == 200
        assert response.json()['canExport'] is False
    db.roles.update_one({'name': 'Portfolio Leader'}, {'$pull': {'permissions': 'export'}})
    assert http.get('/api/reports/applications?format=csv', headers=leader).status_code == 403
    assert db.audit_logs.count_documents({'action': 'export'}) == 0


def test_csv_escapes_formulas_scopes_and_audits(client):
    """Escape untrusted spreadsheet cells and audit each successful export."""
    http, db = client
    headers = login(http)
    db.applications.update_one({'name': 'Payment Gateway'}, {'$set': {'name': '=HYPERLINK("https://example.com")'}})
    db.portfolios.update_one({'name': 'Payments'}, {'$set': {'owner': '  +unsafe'}})
    response = http.get('/api/reports/applications?format=csv&limit=1&offset=1', headers=headers)
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/csv')
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 3
    assert any(row['name'].startswith("'=HYPERLINK") for row in rows)
    assert all(row['portfolioName'] == 'Payments' for row in rows)
    portfolio_csv = http.get('/api/reports/portfolios?format=csv', headers=headers)
    assert portfolio_csv.status_code == 200
    portfolios = list(csv.DictReader(io.StringIO(portfolio_csv.text)))
    assert len(portfolios) == 1 and portfolios[0]['owner'] == "'  +unsafe"
    assert db.audit_logs.count_documents({'action': 'export', 'entityType': 'application'}) == 1
    assert db.audit_logs.count_documents({'action': 'export', 'entityType': 'portfolio'}) == 1


def test_export_all_pages_and_validation(client):
    """Avoid page-size truncation and reject invalid report parameters."""
    http, db = client
    headers = login(http)
    template = db.applications.find_one({'name': 'Payment Gateway'})
    template.pop('_id')
    db.applications.insert_many([{**template, 'name': f'Bulk {i:03d}'} for i in range(105)])
    response = http.get('/api/reports/applications?format=csv&search=Bulk&limit=1', headers=headers)
    assert response.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(response.text)))) == 105
    for query in ('limit=101', 'offset=-1', 'portfolioId=%24ne', 'riskLevel=invalid', 'format=x'):
        assert http.get(f'/api/reports/applications?{query}', headers=headers).status_code == 422
    assert http.get('/api/reports/portfolios?status=invalid', headers=headers).status_code == 422


@pytest_asyncio.fixture
async def report_db():
    """Provide a disposable real MongoDB for direct service tests."""
    mongo = AsyncMongoClient(os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), serverSelectionTimeoutMS=5000)
    name = 'ketsip_report_service_test_' + uuid4().hex
    try:
        await mongo.admin.command('ping')
        db = mongo[name]
        await db.roles.insert_one({'name': 'Portfolio Leader', 'permissions': ['view', 'export']})
        yield db
    finally:
        await mongo.drop_database(name)
        await mongo.close()


@pytest_asyncio.fixture
async def report_user(report_db):
    """Persist an active actor for transactional authorization checks."""
    user = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader', 'status': 'Active'}
    await report_db.users.insert_one(user)
    return user


@pytest.mark.asyncio
async def test_export_rejects_inconsistent_page_without_audit(report_db, report_user):
    """An empty page before the reported total is an error, not a partial export."""
    user = report_user
    offsets = []

    async def page(actor, size, offset):
        offsets.append((size, offset))
        return {'items': [{'name': 'First'}] if offset == 0 else [], 'total': 2}

    with pytest.raises(HTTPException) as error:
        await service.export_csv(report_db, user, 'applications', page)
    assert error.value.status_code == 409
    assert error.value.detail == 'Report changed during export; retry'
    assert offsets == [(100, 0), (100, 1)]
    assert await report_db.audit_logs.count_documents({'action': 'export'}) == 0


@pytest.mark.asyncio
async def test_export_rechecks_live_permission_after_reading_pages(report_db, report_user, monkeypatch):
    """Revoking export after a page transaction prevents output and audit."""
    user = report_user
    assert await service.can_export(report_db, user) is True
    pages = []
    original_step = service.export_step

    async def page(actor, size, offset):
        assert actor['_id'] == user['_id']
        assert (size, offset) == (100, 0)
        pages.append(offset)
        return {'items': [{'name': 'Sensitive'}], 'total': 1}

    async def revoke_after_page(*args, **kwargs):
        result = await original_step(*args, **kwargs)
        if pages:
            # The page transaction has committed and released the role lock.
            await report_db.roles.update_one({'name': 'Portfolio Leader'}, {'$pull': {'permissions': 'export'}})
        return result

    monkeypatch.setattr(service, 'export_step', revoke_after_page)
    with pytest.raises(HTTPException) as error:
        await service.export_csv(report_db, user, 'portfolios', page)
    assert error.value.status_code == 403
    assert error.value.detail == 'Export is not permitted'
    assert pages == [0]
    assert await service.can_export(report_db, user) is False
    assert await report_db.audit_logs.count_documents({'action': 'export'}) == 0


@pytest.mark.asyncio
async def test_export_propagates_page_failure_without_audit(report_db, report_user):
    """A failed page fetch cannot produce a successful audited export."""
    user = report_user

    async def page(actor, size, offset):
        raise RuntimeError('page unavailable')

    with pytest.raises(RuntimeError, match='page unavailable'):
        await service.export_csv(report_db, user, 'applications', page)
    assert await report_db.audit_logs.count_documents({'action': 'export'}) == 0


@pytest.mark.asyncio
async def test_export_denied_before_fetching_when_grant_missing(report_db, report_user):
    """A missing export grant stops the export before accessing pages."""
    user = report_user
    await report_db.roles.update_one({'name': user['role']}, {'$pull': {'permissions': 'export'}})
    calls = []

    async def page(actor, size, offset):
        calls.append((size, offset))
        return {'items': [], 'total': 0}

    with pytest.raises(HTTPException) as error:
        await service.export_csv(report_db, user, 'applications', page)
    assert error.value.status_code == 403
    assert calls == []
    assert await report_db.audit_logs.count_documents({'action': 'export'}) == 0
