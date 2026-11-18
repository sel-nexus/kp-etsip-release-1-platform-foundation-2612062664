"""Verify application security boundaries with the real MongoDB engine."""

import os
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def security_client():
    """Use and remove a uniquely named seeded database on a local test MongoDB."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true')
    name = 'ketsip_security_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri=uri, mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def test_raw_text_round_trips_as_json_without_treating_it_as_html(security_client):
    """Raw storage is data, not evidence of execution in a React consumer."""
    http, db = security_client
    login = http.post('/api/auth/login', json={'email': 'leader@ketsip.local', 'password': 'Initial-Demo-Password123!'})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    portfolio = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    hostile = '<script>window.inventoryXss = true</script><img src=x onerror="window.inventoryXss = true">'
    created = http.post('/api/applications', headers=headers, json={'name': 'Security boundary record', 'portfolioId': portfolio, 'description': hostile})
    assert created.status_code == 201
    ident = created.json()['id']
    assert db.applications.find_one({'_id': ObjectId(ident)})['description'] == hostile
    detail = http.get(f'/api/applications/{ident}', headers=headers)
    assert detail.status_code == 200
    assert detail.headers['content-type'].startswith('application/json')
    assert detail.json()['description'] == hostile


def test_url_schemes_and_nosql_objects_are_rejected_without_mutation(security_client):
    """Reject unsafe URLs and operator objects on both create and update."""
    http, db = security_client
    login = http.post('/api/auth/login', json={'email': 'leader@ketsip.local', 'password': 'Initial-Demo-Password123!'})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    portfolio = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    original = db.applications.find_one({'name': 'Payment Gateway'})
    valid = {'name': 'Security boundary record', 'portfolioId': portfolio, 'repositoryLink': 'https://example.com/repo', 'pipelineLink': 'http://example.com/build'}
    count = db.applications.count_documents({})
    events = db.audit_logs.count_documents({'entityType': 'application'})
    for field, value in [('repositoryLink', 'javascript:alert(1)'), ('pipelineLink', 'data:text/html,<script>alert(1)</script>'), ('repositoryLink', 'file:///etc/passwd'), ('pipelineLink', '//evil.example/path'), ('portfolioId', {'$ne': None}), ('name', {'$regex': '.*'})]:
        bad = {**valid, field: value}
        assert http.post('/api/applications', headers=headers, json=bad).status_code == 422
        assert http.put(f"/api/applications/{original['_id']}", headers=headers, json=bad).status_code == 422
        assert db.applications.count_documents({}) == count
        assert db.applications.find_one({'_id': original['_id']}) == original
        assert db.audit_logs.count_documents({'entityType': 'application'}) == events
    created = http.post('/api/applications', headers=headers, json=valid)
    assert created.status_code == 201
    assert created.json()['repositoryLink'] == valid['repositoryLink']
    assert created.json()['pipelineLink'] == valid['pipelineLink']


def test_operator_login_regex_search_and_encoded_paths_do_not_bypass_scope(security_client):
    """Do not interpret query operators, regex syntax or encoded path traversal."""
    http, db = security_client
    for credentials in [
        {'email': {'$ne': None}, 'password': 'anything'},
        {'email': 'leader@ketsip.local', 'password': {'$ne': None}},
    ]:
        response = http.post('/api/auth/login', json=credentials)
        assert response.status_code == 422
        assert 'token' not in response.json()
    login = http.post('/api/auth/login', json={'email': 'leader@ketsip.local', 'password': 'Initial-Demo-Password123!'})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    normal = http.get('/api/applications', headers=headers, params={'search': 'Payment Gateway'})
    assert normal.status_code == 200 and normal.json()['total'] >= 1
    regex = http.get('/api/applications', headers=headers, params={'search': '.*'})
    assert regex.status_code == 200 and regex.json()['total'] == 0
    assert http.get('/api/applications?portfolioId=%24ne', headers=headers).status_code == 422
    outside = db.applications.find_one({'name': 'Customer Profile'})
    for path in (f"/api/applications/%2e%2e%2fapplications%2f{outside['_id']}", f"/api/applications/%252e%252e%252fapplications%252f{outside['_id']}"):
        response = http.get(path, headers=headers)
        assert response.status_code == 404
        assert outside['name'] not in response.text
        assert str(outside['_id']) not in response.text
