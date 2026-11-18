"""Exercise portfolio HTTP authorization and persistence on disposable MongoDB."""

from uuid import uuid4
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    """Run the real app against a unique temporary database."""
    name = 'ketsip_portfolio_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri='mongodb://127.0.0.1:27017', mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def login(http, role='admin'):
    """Sign in with the seeded role and return bearer headers."""
    password = 'Initial-Admin-Password123!' if role == 'admin' else 'Initial-Demo-Password123!'
    response = http.post('/api/auth/login', json={'email': f'{role}@ketsip.local', 'password': password})
    return {'Authorization': 'Bearer ' + response.json()['token']}


def admin(http):
    """Change the first-login password and return an unrestricted token."""
    headers = login(http)
    changed = http.post('/api/auth/password', headers=headers, json={'currentPassword': 'Initial-Admin-Password123!', 'newPassword': 'Changed-Admin-Password123!'})
    return {'Authorization': 'Bearer ' + changed.json()['token']}


def test_scoped_list_filters_and_aggregates(client):
    """Compute real averages, apply literal search and filter by application area."""
    http, db = client
    headers = login(http, 'leader')
    page = http.get('/api/portfolios', headers=headers).json()
    assert page['total'] == 1 and page['items'][0]['name'] == 'Payments'
    assert page['items'][0]['applicationCount'] == 3 and page['items'][0]['qualityScore'] == 76
    assert http.get('/api/portfolios?productArea=Data', headers=headers).json()['total'] == 0
    assert http.get('/api/portfolios?productArea=Payments&status=Active', headers=headers).json()['total'] == 1
    assert http.get('/api/portfolios?search=.*', headers=headers).json()['total'] == 0
    other = db.portfolios.find_one({'name': 'Customer Platform'})
    assert http.get(f"/api/portfolios/{other['_id']}", headers=headers).status_code == 403
    assert http.get(f"/api/portfolios/{other['_id']}/applications", headers=headers).status_code == 403
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    assert http.get('/api/portfolios', headers=headers).json()['total'] == 0


def test_auth_validation_mutations_and_audit(client):
    """Require initial password change, validate and audit writes and references."""
    http, db = client
    assert http.get('/api/portfolios').status_code == 401
    restricted = login(http)
    assert http.get('/api/portfolios', headers=restricted).status_code == 403
    headers = admin(http)
    assert http.get('/api/portfolios?limit=101', headers=headers).status_code == 422
    first = http.get('/api/portfolios?limit=1&offset=0', headers=headers).json()
    second = http.get('/api/portfolios?limit=1&offset=1', headers=headers).json()
    assert first['total'] == 3 and second['total'] == 3 and first['items'][0]['id'] != second['items'][0]['id']
    assert http.get('/api/portfolios?status=Invalid', headers=headers).status_code == 422
    assert http.get('/api/portfolios/not-an-id', headers=headers).status_code == 404
    assert http.post('/api/portfolios', headers=headers, json={'name': '   '}).status_code == 422
    body = {'name': 'New Portfolio', 'owner': 'Owner', 'description': 'Details', 'productArea': 'Area', 'status': 'Active'}
    created = http.post('/api/portfolios', headers=headers, json=body)
    assert created.status_code == 201 and created.json()['qualityScore'] is None
    ident = created.json()['id']
    assert http.post('/api/portfolios', headers=headers, json=body).status_code == 409
    assert http.put(f'/api/portfolios/{ident}', headers=headers, json={**body, 'status': 'Archived'}).json()['status'] == 'Archived'
    assert http.get(f'/api/portfolios/{ident}/applications', headers=headers).json() == {'items': [], 'total': 0}
    payments = db.portfolios.find_one({'name': 'Payments'})
    assert http.delete(f"/api/portfolios/{payments['_id']}", headers=headers).status_code == 409
    assert http.delete(f'/api/portfolios/{ident}', headers=headers).status_code == 204
    assert http.get(f'/api/portfolios/{ident}', headers=headers).status_code == 404
    assert sorted(e['action'] for e in db.audit_logs.find({'entityType': 'portfolio', 'entityId': ident})) == ['create', 'delete', 'edit']


def test_fixed_roles_and_editable_permissions(client):
    """Enforce assigned leader editing and fixed restrictions even if grants change."""
    http, db = client
    leader = login(http, 'leader')
    engineer = login(http, 'engineer')
    readonly = login(http, 'readonly')
    payments = db.portfolios.find_one({'name': 'Payments'})
    ident = str(payments['_id'])
    body = {'name': 'Payments revised', 'owner': 'Team', 'description': '', 'productArea': 'Payments', 'status': 'Active'}
    assert http.put(f'/api/portfolios/{ident}', headers=leader, json=body).status_code == 200
    db.roles.update_one({'name': 'Engineer'}, {'$set': {'permissions': ['view', 'create', 'edit', 'delete']}})
    assert http.put(f'/api/portfolios/{ident}', headers=engineer, json=body).status_code == 403
    assert http.post('/api/portfolios', headers=engineer, json=body).status_code == 403
    assert http.delete(f'/api/portfolios/{ident}', headers=engineer).status_code == 403
    assert http.put(f'/api/portfolios/{ident}', headers=readonly, json=body).status_code == 403
    db.roles.update_one({'name': 'Portfolio Leader'}, {'$set': {'permissions': ['view']}})
    assert http.put(f'/api/portfolios/{ident}', headers=leader, json=body).status_code == 403
    assert http.get(f'/api/portfolios/{ident}', headers=leader).json()['canEdit'] is False
    db.roles.update_one({'name': 'Portfolio Leader'}, {'$set': {'permissions': []}})
    assert http.get('/api/portfolios', headers=leader).status_code == 403


def test_leader_edit_assigned_portfolio_persists_and_is_audited(client):
    """An assigned leader can edit and subsequently read the persisted values."""
    http, db = client
    headers = login(http, 'leader')
    payments = db.portfolios.find_one({'name': 'Payments'})
    ident = str(payments['_id'])
    body = {'name': 'Payments updated', 'owner': 'New team', 'description': 'Updated details', 'productArea': 'Payments', 'status': 'Archived'}

    response = http.put(f'/api/portfolios/{ident}', headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()['canEdit'] is True
    assert {key: response.json()[key] for key in body} == body
    stored = db.portfolios.find_one({'_id': payments['_id']})
    assert {key: stored[key] for key in body} == body
    detail = http.get(f'/api/portfolios/{ident}', headers=headers)
    assert detail.status_code == 200
    assert {key: detail.json()[key] for key in body} == body
    events = list(db.audit_logs.find({'entityType': 'portfolio', 'entityId': ident, 'action': 'edit'}))
    assert len(events) == 1
    assert events[0]['userId'] == db.users.find_one({'email': 'leader@ketsip.local'})['_id']


def test_leader_cannot_edit_unassigned_portfolio_or_change_its_state(client):
    """A leader's generic edit grant does not allow edits outside assigned scope."""
    http, db = client
    headers = login(http, 'leader')
    other = db.portfolios.find_one({'name': 'Customer Platform'})
    ident = str(other['_id'])
    body = {'name': 'Unauthorized edit', 'owner': 'Intruder', 'description': 'Changed', 'productArea': 'Customer', 'status': 'Archived'}
    before_events = list(db.audit_logs.find({'entityType': 'portfolio', 'entityId': ident}))

    response = http.put(f'/api/portfolios/{ident}', headers=headers, json=body)
    assert response.status_code == 403
    assert db.portfolios.find_one({'_id': other['_id']}) == other
    assert list(db.audit_logs.find({'entityType': 'portfolio', 'entityId': ident})) == before_events
    assert http.post('/api/portfolios', headers=headers, json=body).status_code == 403
    assert db.portfolios.find_one({'name': body['name']}) is None


def test_detail_and_linked_applications_are_scoped_and_live(client):
    """Details aggregate only linked applications and never expose another portfolio."""
    http, db = client
    headers = login(http, 'leader')
    payments = db.portfolios.find_one({'name': 'Payments'})
    other = db.portfolios.find_one({'name': 'Customer Platform'})
    ident = str(payments['_id'])
    other_id = str(other['_id'])

    detail = http.get(f'/api/portfolios/{ident}', headers=headers)
    assert detail.status_code == 200
    assert detail.json()['applicationCount'] == 3
    assert detail.json()['qualityScore'] == 76
    assert detail.json()['canEdit'] is True
    first = http.get(f'/api/portfolios/{ident}/applications?limit=2&offset=0', headers=headers)
    second = http.get(f'/api/portfolios/{ident}/applications?limit=2&offset=2', headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()['total'] == second.json()['total'] == 3
    items = first.json()['items'] + second.json()['items']
    expected = list(db.applications.find({'portfolioId': ident}).sort([('name', 1), ('_id', 1)]))
    assert [item['id'] for item in items] == [str(app['_id']) for app in expected]
    assert [item['name'] for item in items] == ['Billing', 'Payment Gateway', 'Settlement']
    assert all(set(item) == {'id', 'name', 'productArea', 'qualityScore', 'riskLevel'} for item in items)
    assert not ({item['id'] for item in items} & {str(app['_id']) for app in db.applications.find({'portfolioId': other_id})})

    db.applications.update_one({'name': 'Payment Gateway'}, {'$set': {'qualityScore': 100}})
    refreshed = http.get(f'/api/portfolios/{ident}', headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()['applicationCount'] == 3
    assert refreshed.json()['qualityScore'] == 84
    for suffix in ('', '/applications'):
        denied = http.get(f'/api/portfolios/{other_id}{suffix}', headers=headers)
        assert denied.status_code == 403
        assert 'Customer Profile' not in denied.text
        assert 'Customer Platform' not in denied.text
