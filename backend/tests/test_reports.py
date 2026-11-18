"""Exercise reports through HTTP against disposable MongoDB databases."""

import csv
import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    """Start an isolated seeded API and discard its test database."""
    name = 'ketsip_reports_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri='mongodb://127.0.0.1:27017', mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
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
    assert http.get(f'/api/reports/applications?portfolioId={other}', headers=headers).json()['total'] == 0
    assert http.get(f'/api/reports/applications?portfolioId={payments}&riskLevel=High', headers=headers).json()['total'] == 1
    assert http.get('/api/reports/applications?search=.*', headers=headers).json()['total'] == 0
    report = http.get('/api/reports/portfolios?search=Payments', headers=headers).json()
    assert report['total'] == 1 and report['items'][0]['applicationCount'] == 3
    assert http.get('/api/reports/portfolios?search=Customer', headers=headers).json()['total'] == 0
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    assert http.get('/api/reports/portfolios', headers=headers).json()['total'] == 0
    assert http.get('/api/reports/applications', headers=headers).json()['total'] == 0


def test_export_permissions_are_live_and_fixed(client):
    """Deny read-only exports even if granted and honor revocation immediately."""
    http, db = client
    leader = login(http)
    readonly = login(http, 'readonly')
    assert http.get('/api/reports/applications').status_code == 401
    db.roles.update_one({'name': 'Read Only'}, {'$addToSet': {'permissions': 'export'}})
    for kind in ('applications', 'portfolios'):
        assert http.get(f'/api/reports/{kind}?format=csv', headers=readonly).status_code == 403
        assert http.get(f'/api/reports/{kind}', headers=readonly).json()['canExport'] is False
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
    assert len(list(csv.DictReader(io.StringIO(response.text)))) == 105
    for query in ('limit=101', 'offset=-1', 'portfolioId=%24ne', 'riskLevel=invalid', 'format=x'):
        assert http.get(f'/api/reports/applications?{query}', headers=headers).status_code == 422
    assert http.get('/api/reports/portfolios?status=invalid', headers=headers).status_code == 422
