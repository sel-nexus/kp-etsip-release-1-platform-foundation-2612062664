"""Exercise administration and audit endpoints with a real disposable MongoDB."""

import os
from datetime import datetime, timezone
from uuid import uuid4
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    """Create and remove an isolated database for each test."""
    name = 'ketsip_admin_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri=os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
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
    fetched = http.get('/api/users/' + user['id'], headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()['name'] == 'New engineer'
    changed = http.put('/api/users/' + user['id'], headers=headers, json={**body, 'role': 'Read Only', 'password': None})
    assert changed.status_code == 200 and changed.json()['role'] == 'Read Only'
    stored = db.users.find_one({'_id': ObjectId(user['id'])})
    assert stored['role'] == 'Read Only' and stored['portfolioAccess'] == [portfolio]
    assert http.delete('/api/users/' + user['id'], headers=headers).status_code == 204
    assert db.users.find_one({'email': user['email']})['status'] == 'Inactive'
    assert db.users.find_one({'_id': ObjectId(user['id'])})['tokenVersion'] == stored['tokenVersion'] + 1
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
    response = http.get('/api/roles', headers=headers)
    assert response.status_code == 200
    roles = response.json()['items']
    role = next(role for role in roles if role['name'] == 'Engineer')
    assert http.post('/api/roles', headers=headers, json={'name': 'Engineer', 'description': '', 'permissions': []}).status_code == 409
    assert http.post('/api/roles', headers=headers, json={'name': 'Other', 'description': '', 'permissions': []}).status_code == 422
    assert http.put('/api/roles/' + role['id'], headers=headers, json={'name': 'Engineer', 'description': 'Restricted', 'permissions': ['view']}).status_code == 200
    stored_role = db.roles.find_one({'_id': ObjectId(role['id'])})
    assert stored_role['description'] == 'Restricted' and stored_role['permissions'] == ['view']
    engineer = login(http, 'engineer@ketsip.local', 'Initial-Demo-Password123!')
    applications = http.get('/api/applications', headers=engineer)
    assert applications.status_code == 200
    assert applications.json()['canCreate'] is False
    payment = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    customer = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    engineer_doc = db.users.find_one({'email': 'engineer@ketsip.local'})
    payment_before = http.get('/api/applications?portfolioId=' + payment, headers=engineer)
    assert payment_before.status_code == 200
    assert payment_before.json()['total'] == 3
    updated = {'name': engineer_doc['name'], 'email': engineer_doc['email'], 'role': 'Engineer', 'portfolioAccess': [customer], 'status': 'Active'}
    assert http.put('/api/users/' + str(engineer_doc['_id']), headers=headers, json=updated).status_code == 200
    payment_after = http.get('/api/applications?portfolioId=' + payment, headers=engineer)
    customer_after = http.get('/api/applications?portfolioId=' + customer, headers=engineer)
    assert payment_after.status_code == 200 and customer_after.status_code == 200
    assert payment_after.json()['total'] == 0
    assert customer_after.json()['total'] == 3
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


@pytest.mark.parametrize(('params', 'expected'), [
    ({'from': '2024-01-02T10:00:00Z'}, {'start', 'middle', 'end', 'after'}),
    ({'to': '2024-01-02T12:00:00Z'}, {'before', 'start', 'middle', 'end'}),
    ({'from': '2024-01-02T10:00:00Z', 'to': '2024-01-02T12:00:00Z'}, {'start', 'middle', 'end'}),
    ({'from': '2024-01-02T15:30:00+05:30', 'to': '2024-01-02T07:00:00-05:00'}, {'start', 'middle', 'end'}),
    ({'from': '2024-01-02T13:00:00+02:00', 'to': '2024-01-02T06:00:00-05:00'}, {'middle'}),
    ({'from': '2024-01-03T00:00:00Z'}, set()),
])
def test_audit_date_filters_include_boundaries_and_normalize_offsets(client, params, expected):
    """Filter stored UTC instants inclusively, including single-sided ranges."""
    http, db = client
    headers = admin(http)
    actor = db.users.find_one({'role': 'Administrator'})
    times = {
        'before': datetime(2024, 1, 2, 9, 59, 59, 999000, tzinfo=timezone.utc),
        'start': datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
        'middle': datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        'end': datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
        'after': datetime(2024, 1, 2, 12, 0, 0, 1000, tzinfo=timezone.utc),
    }
    db.audit_logs.insert_many([
        {'timestamp': timestamp, 'userId': actor['_id'], 'userName': actor['name'],
         'action': 'date-range-test', 'entityType': 'test', 'entityId': label, 'details': ''}
        for label, timestamp in times.items()
    ])
    response = http.get('/api/audit-logs', headers=headers,
                        params={'action': 'date-range-test', **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == len(expected)
    assert {item['entityId'] for item in body['items']} == expected
    assert len(body['items']) == len(expected)


@pytest.mark.parametrize('params', [
    {'from': 'not-a-date'},
    {'to': '2024-02-30T12:00:00Z'},
    {'from': '2024-01-02T10:00:00'},
    {'to': '2024-01-02T12:00:00'},
    {'from': '2024-01-02T12:00:00Z', 'to': '2024-01-02T10:00:00Z'},
    {'from': '2024-01-02T10:00:00-05:00', 'to': '2024-01-02T12:00:00+02:00'},
])
def test_audit_rejects_invalid_or_timezone_naive_date_ranges(client, params):
    """Reject malformed, timezone-naive, and chronologically reversed bounds."""
    http, db = client
    headers = admin(http)
    actor = db.users.find_one({'role': 'Administrator'})
    db.audit_logs.insert_one({
        'timestamp': datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        'userId': actor['_id'], 'userName': actor['name'], 'action': 'date-range-test',
        'entityType': 'test', 'entityId': 'existing', 'details': '',
    })
    response = http.get('/api/audit-logs', headers=headers,
                        params={'action': 'date-range-test', **params})
    assert response.status_code == 422, response.text
    assert 'detail' in response.json()


def test_role_creation_and_failure_paths(client):
    """A missing supported role can be restored, but conflicts cannot mutate it."""
    http, db = client
    headers = admin(http)
    assert db.roles.delete_one({'name': 'Engineer'}).deleted_count == 1
    body = {'name': 'Engineer', 'description': 'Restored', 'permissions': ['view', 'edit']}
    response = http.post('/api/roles', headers=headers, json=body)
    assert response.status_code == 201, response.text
    role = response.json()
    assert role['name'] == 'Engineer' and role['permissions'] == ['view', 'edit']
    stored = db.roles.find_one({'_id': ObjectId(role['id'])})
    assert stored['description'] == 'Restored' and stored['permissions'] == body['permissions']
    assert db.audit_logs.count_documents({'entityId': role['id'], 'action': 'create'}) == 1
    assert db.audit_logs.count_documents({'entityId': role['id'], 'action': 'permissionChange'}) == 1
    assert http.post('/api/roles', headers=headers, json=body).status_code == 409
    assert http.put('/api/roles/' + role['id'], headers=headers, json={**body, 'name': 'Auditor'}).status_code == 409
    assert http.put('/api/roles/' + str(ObjectId()), headers=headers, json=body).status_code == 404
    assert http.put('/api/roles/bad-id', headers=headers, json=body).status_code == 404
    assert db.roles.find_one({'_id': ObjectId(role['id'])})['permissions'] == body['permissions']


def test_user_and_role_request_validation(client):
    """Reject missing, null, wrong-type, oversized, and invalid field values."""
    http, db = client
    headers = admin(http)
    user = {'name': 'Valid', 'email': 'valid@example.com', 'role': 'Engineer',
            'portfolioAccess': [], 'status': 'Active', 'password': 'Valid-Password-123!'}
    for field in ('name', 'email', 'role', 'password'):
        payload = {k: v for k, v in user.items() if k != field}
        response = http.post('/api/users', headers=headers, json=payload)
        assert response.status_code == 422, (field, response.text)
    invalid_users = [
        {'name': None}, {'email': None}, {'role': None}, {'password': None},
        {'name': 12}, {'email': {'$ne': ''}}, {'role': []}, {'portfolioAccess': 'bad'},
        {'status': None}, {'vendorIndicator': {}}, {'name': '  '}, {'name': 'x' * 201},
        {'email': 'x' * 250 + '@a.co'}, {'managerName': 'x' * 201},
        {'password': 'x' * 11}, {'password': 'x' * 73}, {'password': 'é' * 37},
        {'portfolioAccess': ['x'] * 1001}, {'status': 'Unknown'}, {'extra': 'bad'},
        {'portfolioAccess': [str(ObjectId())]},
    ]
    portfolio = str(db.portfolios.find_one()['_id'])
    invalid_users.append({'portfolioAccess': [portfolio, portfolio]})
    for changes in invalid_users:
        response = http.post('/api/users', headers=headers, json={**user, **changes})
        assert response.status_code == 422, (changes, response.status_code, response.text)
    assert db.users.count_documents({'email': user['email']}) == 0

    role = {'name': 'Engineer', 'description': 'description', 'permissions': ['view']}
    invalid_roles = [
        {'description': 'description', 'permissions': ['view']},
        {**role, 'name': None}, {**role, 'name': 1}, {**role, 'description': None},
        {**role, 'description': 'x' * 501}, {**role, 'permissions': None},
        {**role, 'permissions': 'view'}, {**role, 'permissions': ['view', 'view']},
        {**role, 'permissions': ['invalid']}, {**role, 'permissions': ['view'] * 7},
        {**role, 'extra': True},
    ]
    for payload in invalid_roles:
        response = http.post('/api/roles', headers=headers, json=payload)
        assert response.status_code == 422, (payload, response.status_code, response.text)
    assert db.roles.count_documents({'name': 'Engineer'}) == 1


def test_admin_list_and_missing_user_statuses(client):
    """Bound list requests and report malformed or missing account identifiers."""
    http, db = client
    headers = admin(http)
    for path in ('/api/users', '/api/roles', '/api/admin/portfolios'):
        response = http.get(path + '?limit=1&offset=0', headers=headers)
        assert response.status_code == 200
        assert len(response.json()['items']) == 1 and response.json()['total'] >= 1
        assert http.get(path + '?limit=0', headers=headers).status_code == 422
        assert http.get(path + '?offset=-1', headers=headers).status_code == 422
    user = {'name': 'Missing', 'email': 'missing@example.com', 'role': 'Engineer'}
    for ident in ('bad-id', str(ObjectId())):
        assert http.get('/api/users/' + ident, headers=headers).status_code == 404
        assert http.put('/api/users/' + ident, headers=headers, json=user).status_code == 404
        assert http.delete('/api/users/' + ident, headers=headers).status_code == 404
    assert db.users.count_documents({'email': 'missing@example.com'}) == 0
