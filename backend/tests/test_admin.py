"""Exercise administration and audit endpoints with a real disposable MongoDB."""

from uuid import uuid4
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    """Create and remove an isolated database for each test."""
    name = 'ketsip_admin_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri='mongodb://127.0.0.1:27017', mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def login(http, email, password):
    """Return a bearer header from a real login."""
    response = http.post('/api/auth/login', json={'email': email, 'password': password})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def admin(http):
    """Complete the seeded administrator's initial password change."""
    headers = login(http, 'admin@ketsip.local', 'Initial-Admin-Password123!')
    response = http.post('/api/auth/password', headers=headers, json={'currentPassword': 'Initial-Admin-Password123!', 'newPassword': 'Changed-Admin-Password123!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_guards_and_live_permissions(client):
    """Enforce first-login guard and fixed administrator and auditor roles."""
    http, db = client
    assert http.get('/api/users').status_code == 401
    pending = login(http, 'admin@ketsip.local', 'Initial-Admin-Password123!')
    assert http.get('/api/users', headers=pending).status_code == 403
    auditor = login(http, 'auditor@ketsip.local', 'Initial-Demo-Password123!')
    assert http.get('/api/users', headers=auditor).status_code == 403
    assert http.get('/api/audit-logs', headers=auditor).status_code == 200
    assert http.get('/api/audit-logs?format=csv', headers=auditor).status_code == 200
    db.roles.update_one({'name': 'Auditor'}, {'$set': {'permissions': ['administer', 'view']}})
    assert http.get('/api/users', headers=auditor).status_code == 403
    assert http.get('/api/audit-logs?format=csv', headers=auditor).status_code == 403


def test_user_crud_conflicts_and_deactivation(client):
    """Persist normalized accounts, reject conflicts and retain audit history."""
    http, db = client
    headers = admin(http)
    portfolio = str(db.portfolios.find_one()['_id'])
    body = {'name': 'New engineer', 'email': 'NEW@example.com', 'role': 'Engineer', 'portfolioAccess': [portfolio], 'status': 'Active', 'password': 'Very-Strong-Password123!'}
    created = http.post('/api/users', headers=headers, json=body)
    assert created.status_code == 201, created.text
    user = created.json()
    assert user['email'] == 'new@example.com' and 'passwordHash' not in user
    assert db.users.find_one({'email': user['email']})['passwordHash'].startswith('$2')
    assert http.post('/api/users', headers=headers, json=body).status_code == 409
    assert http.post('/api/users', headers=headers, json={**body, 'email': 'bad'}).status_code == 422
    assert http.post('/api/users', headers=headers, json={**body, 'email': 'other@example.com', 'portfolioAccess': ['bad']}).status_code == 422
    assert http.get('/api/users/' + user['id'], headers=headers).json()['name'] == 'New engineer'
    changed = http.put('/api/users/' + user['id'], headers=headers, json={**body, 'role': 'Read Only', 'password': None})
    assert changed.status_code == 200 and changed.json()['role'] == 'Read Only'
    assert http.delete('/api/users/' + user['id'], headers=headers).status_code == 204
    assert db.users.find_one({'email': user['email']})['status'] == 'Inactive'
    assert http.post('/api/auth/login', json={'email': user['email'], 'password': body['password']}).status_code == 401
    assert db.audit_logs.count_documents({'entityId': user['id'], 'action': 'roleChange'}) == 1
    assert db.audit_logs.count_documents({'entityId': user['id'], 'action': 'delete'}) == 1
    assert 'Very-Strong-Password123!' not in str(list(db.audit_logs.find()))


def test_role_edits_are_live_and_last_admin_protected(client):
    """Enforce role vocabulary and prevent ordinary last-admin lockout."""
    http, db = client
    headers = admin(http)
    admin_id = str(db.users.find_one({'role': 'Administrator'})['_id'])
    assert http.delete('/api/users/' + admin_id, headers=headers).status_code == 409
    roles = http.get('/api/roles', headers=headers).json()['items']
    role = next(role for role in roles if role['name'] == 'Engineer')
    assert http.post('/api/roles', headers=headers, json={'name': 'Engineer', 'description': '', 'permissions': []}).status_code == 409
    assert http.post('/api/roles', headers=headers, json={'name': 'Other', 'description': '', 'permissions': []}).status_code == 422
    assert http.put('/api/roles/' + role['id'], headers=headers, json={'name': 'Engineer', 'description': 'Restricted', 'permissions': ['view']}).status_code == 200
    engineer = login(http, 'engineer@ketsip.local', 'Initial-Demo-Password123!')
    assert http.get('/api/applications', headers=engineer).json()['canCreate'] is False
    payment = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    customer = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    engineer_doc = db.users.find_one({'email': 'engineer@ketsip.local'})
    assert http.get('/api/applications?portfolioId=' + payment, headers=engineer).json()['total'] == 3
    updated = {'name': engineer_doc['name'], 'email': engineer_doc['email'], 'role': 'Engineer', 'portfolioAccess': [customer], 'status': 'Active'}
    assert http.put('/api/users/' + str(engineer_doc['_id']), headers=headers, json=updated).status_code == 200
    assert http.get('/api/applications?portfolioId=' + payment, headers=engineer).json()['total'] == 0
    assert http.get('/api/applications?portfolioId=' + customer, headers=engineer).json()['total'] == 3
    assert db.audit_logs.count_documents({'action': 'permissionChange', 'entityId': role['id']}) == 1
    admin_role = next(role for role in roles if role['name'] == 'Administrator')
    assert http.put('/api/roles/' + admin_role['id'], headers=headers, json={'name': 'Administrator', 'permissions': ['view']}).status_code == 409


def test_audit_filters_export_and_formula_safety(client):
    """Filter bounded audit pages and protect CSV consumers from formulas."""
    http, db = client
    headers = admin(http)
    actor = db.users.find_one({'role': 'Administrator'})
    from datetime import datetime, timezone
    db.audit_logs.insert_one({'timestamp': datetime.now(timezone.utc), 'userId': actor['_id'], 'userName': '=HYPERLINK("bad")', 'action': 'edit', 'entityType': 'user', 'entityId': 'x', 'details': '+bad'})
    url = '/api/audit-logs?userId=' + str(actor['_id']) + '&action=edit&limit=100'
    result = http.get(url, headers=headers)
    assert result.status_code == 200 and all(item['action'] == 'edit' for item in result.json()['items'])
    assert http.get('/api/audit-logs?limit=101', headers=headers).status_code == 422
    assert http.get('/api/audit-logs?userId=bad', headers=headers).status_code == 422
    csv = http.get(url + '&format=csv', headers=headers)
    assert csv.status_code == 200 and "'=HYPERLINK" in csv.text and "'+bad" in csv.text
    assert db.audit_logs.count_documents({'action': 'export', 'entityType': 'audit'}) == 1
    readonly = login(http, 'readonly@ketsip.local', 'Initial-Demo-Password123!')
    assert http.get('/api/audit-logs', headers=readonly).status_code == 403
