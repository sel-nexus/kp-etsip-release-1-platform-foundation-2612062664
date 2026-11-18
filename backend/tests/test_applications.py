"""Exercise application API behavior against a disposable MongoDB database."""

import os
from uuid import uuid4
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    """Start the actual API with isolated seeded data."""
    name = 'ketsip_app_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri=os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def login(http, role='leader'):
    """Authenticate a seeded user."""
    password = 'Initial-Admin-Password123!' if role == 'admin' else 'Initial-Demo-Password123!'
    response = http.post('/api/auth/login', json={'email': f'{role}@ketsip.local', 'password': password})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def admin(http):
    """Complete the administrator's first password change."""
    response = http.post('/api/auth/password', headers=login(http, 'admin'), json={'currentPassword': 'Initial-Admin-Password123!', 'newPassword': 'Changed-Admin-Password123!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def payload(portfolio_id):
    """Build a complete valid inventory record."""
    return {'name': 'New App', 'description': 'Description', 'portfolioId': portfolio_id, 'productArea': 'Payments', 'businessOwner': 'Business', 'technologyOwner': 'Technology', 'etsOwner': 'ETS', 'criticality': 'Critical', 'tier': 4, 'lifecycleStatus': 'Deprecated', 'technologyStack': 'Python', 'repositoryLink': 'https://example.com/repo', 'pipelineLink': None, 'applicationHealthScore': 100, 'qualityScore': 0, 'automationCoverage': 75, 'unitTestCoverage': 80, 'openDefects': 2, 'openCriticalDefects': 1, 'riskLevel': 'High'}


def test_scoped_search_filters_and_pagination(client):
    """Filter seeded records, escape regex and respect assignment."""
    http, db = client
    headers = login(http)
    payments = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    other = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    response = http.get('/api/applications?limit=1&offset=1', headers=headers)
    assert response.status_code == 200
    page = response.json()
    assert page['total'] == 3 and len(page['items']) == 1
    response = http.get('/api/applications?search=.*', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    response = http.get(f'/api/applications?portfolioId={payments}&criticality=High&riskLevel=High&lifecycleStatus=Active', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 1
    response = http.get(f'/api/applications?portfolioId={other}', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    assert http.get('/api/applications?limit=101', headers=headers).status_code == 422
    assert http.get('/api/applications?portfolioId=%24ne', headers=headers).status_code == 422
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    response = http.get('/api/applications', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0


def test_create_edit_delete_persist_and_audit(client):
    """Persist all fields and audit successful mutations."""
    http, db = client
    headers = admin(http)
    portfolio_id = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    body = payload(portfolio_id)
    response = http.post('/api/applications', json=body, headers=headers)
    assert response.status_code == 201
    ident = response.json()['id']
    stored = db.applications.find_one({'_id': ObjectId(ident)})
    assert all(stored[key] == value for key, value in body.items())
    detail = http.get(f'/api/applications/{ident}', headers=headers)
    assert detail.status_code == 200
    assert detail.json()['portfolioName'] == 'Payments'
    changed = http.put(f'/api/applications/{ident}', json={**body, 'qualityScore': 42}, headers=headers)
    assert changed.status_code == 200 and db.applications.find_one({'_id': ObjectId(ident)})['qualityScore'] == 42
    assert http.delete(f'/api/applications/{ident}', headers=headers).status_code == 204
    assert db.applications.find_one({'_id': ObjectId(ident)}) is None
    assert sorted(event['action'] for event in db.audit_logs.find({'entityType': 'application', 'entityId': ident})) == ['create', 'delete', 'edit']
    assert http.get(f'/api/applications/{ident}', headers=headers).status_code == 404


def test_validation_duplicates_and_missing_references(client):
    """Reject invalid values, duplicate names and nonexistent references."""
    http, db = client
    headers = login(http)
    portfolio_id = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    body = payload(portfolio_id)
    for field, value in [('name', '  '), ('tier', 5), ('qualityScore', 101), ('openDefects', -1), ('automationCoverage', '10'), ('repositoryLink', 'javascript:alert(1)'), ('criticality', 'Unknown'), ('portfolioId', 'bad')]:
        assert http.post('/api/applications', json={**body, field: value}, headers=headers).status_code == 422
    assert http.post('/api/applications', json={**body, 'unexpected': 1}, headers=headers).status_code == 422
    # Admin has unrestricted scope, so a valid but absent portfolio reaches the existence check.
    assert http.post('/api/applications', json={**body, 'portfolioId': str(ObjectId())}, headers=admin(http)).status_code == 404
    assert http.post('/api/applications', json=body, headers=headers).status_code == 201
    assert http.post('/api/applications', json=body, headers=headers).status_code == 409
    assert db.applications.count_documents({'name': body['name']}) == 1


def test_live_permissions_and_cross_portfolio_moves(client):
    """Deny source and destination scope escapes and fixed-role violations."""
    http, db = client
    leader, engineer, readonly = login(http), login(http, 'engineer'), login(http, 'readonly')
    payments = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    other = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    app = db.applications.find_one({'name': 'Payment Gateway'})
    outside = db.applications.find_one({'name': 'Customer Profile'})
    ident = str(app['_id'])
    assert http.get('/api/applications').status_code == 401
    assert http.get(f"/api/applications/{outside['_id']}", headers=leader).status_code == 403
    assert http.put(f'/api/applications/{ident}', json=payload(other), headers=leader).status_code == 403
    assert http.put(f"/api/applications/{outside['_id']}", json=payload(payments), headers=leader).status_code == 403
    assert db.applications.find_one({'_id': app['_id']}) == app
    db.roles.update_one({'name': 'Read Only'}, {'$set': {'permissions': ['view', 'create', 'edit', 'delete']}})
    assert http.post('/api/applications', json=payload(payments), headers=readonly).status_code == 403
    assert http.delete(f'/api/applications/{ident}', headers=engineer).status_code == 403
    db.roles.update_one({'name': 'Engineer'}, {'$set': {'permissions': ['view']}})
    assert http.put(f'/api/applications/{ident}', json=payload(payments), headers=engineer).status_code == 403
    detail = http.get(f'/api/applications/{ident}', headers=engineer)
    assert detail.status_code == 200
    assert detail.json()['canEdit'] is False


def test_invalid_create_and_update_never_change_inventory(client):
    """Reject missing, oversized, malformed and dangerous link fields atomically."""
    http, db = client
    headers = admin(http)
    portfolio_id = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    original = db.applications.find_one({'name': 'Payment Gateway'})
    body = payload(portfolio_id)
    invalid = [{key: value for key, value in body.items() if key != required}
               for required in ('name', 'portfolioId')]
    invalid += [{**body, field: value} for field, value in [
        ('name', None), ('name', 'x' * 201), ('description', 'x' * 4001),
        ('businessOwner', 'x' * 201), ('technologyStack', 'x' * 4001),
        ('qualityScore', True), ('tier', '2'), ('openDefects', {}),
        ('repositoryLink', 'javascript:alert(1)'), ('pipelineLink', 'data:text/html,<script>alert(1)</script>'),
        ('repositoryLink', 'file:///etc/passwd'), ('pipelineLink', '//evil.example/path'),
    ]]
    count = db.applications.count_documents({})
    events = db.audit_logs.count_documents({'entityType': 'application'})
    for bad in invalid:
        created = http.post('/api/applications', headers=headers, json=bad)
        assert created.status_code == 422, (bad, created.text)
        changed = http.put(f"/api/applications/{original['_id']}", headers=headers, json=bad)
        assert changed.status_code == 422, (bad, changed.text)
        assert db.applications.find_one({'_id': original['_id']}) == original
        assert db.applications.count_documents({}) == count
        assert db.audit_logs.count_documents({'entityType': 'application'}) == events


def test_text_payload_remains_json_data_and_encoded_paths_do_not_expose_records(client):
    """Treat hostile text as JSON data and do not resolve traversal to another record."""
    http, db = client
    headers = login(http)
    portfolio_id = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    hostile = '<script>alert("inventory-xss")</script>'
    body = {**payload(portfolio_id), 'description': hostile}
    created = http.post('/api/applications', headers=headers, json=body)
    assert created.status_code == 201
    ident = created.json()['id']
    detail = http.get(f'/api/applications/{ident}', headers=headers)
    assert detail.status_code == 200
    assert detail.headers['content-type'].startswith('application/json')
    assert detail.json()['description'] == hostile
    assert db.applications.find_one({'_id': ObjectId(ident)})['description'] == hostile
    outside = db.applications.find_one({'name': 'Customer Profile'})
    for path in (f"/api/applications/%2e%2e%2fapplications%2f{outside['_id']}",
                 f"/api/applications/%252e%252e%252fapplications%252f{outside['_id']}"):
        response = http.get(path, headers=headers)
        assert response.status_code == 404
        assert outside['name'] not in response.text
        assert str(outside['_id']) not in response.text
