"""Exercise portfolio HTTP authorization and persistence on disposable MongoDB."""

import os
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
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri=os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
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
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def admin(http):
    """Change the first-login password and return an unrestricted token."""
    headers = login(http)
    changed = http.post('/api/auth/password', headers=headers, json={'currentPassword': 'Initial-Admin-Password123!', 'newPassword': 'Changed-Admin-Password123!'})
    assert changed.status_code == 200
    return {'Authorization': 'Bearer ' + changed.json()['token']}


def test_scoped_list_filters_and_aggregates(client):
    """Compute real averages, apply literal search and filter by portfolio area."""
    http, db = client
    headers = login(http, 'leader')
    response = http.get('/api/portfolios', headers=headers)
    assert response.status_code == 200
    page = response.json()
    assert page['total'] == 1 and page['items'][0]['name'] == 'Payments'
    assert page['items'][0]['applicationCount'] == 3 and page['items'][0]['qualityScore'] == 76
    response = http.get('/api/portfolios?productArea=Data', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    response = http.get('/api/portfolios?productArea=Payments&status=Active', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 1
    response = http.get('/api/portfolios?search=.*', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0
    other = db.portfolios.find_one({'name': 'Customer Platform'})
    assert http.get(f"/api/portfolios/{other['_id']}", headers=headers).status_code == 403
    assert http.get(f"/api/portfolios/{other['_id']}/applications", headers=headers).status_code == 403
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    response = http.get('/api/portfolios', headers=headers)
    assert response.status_code == 200
    assert response.json()['total'] == 0


def test_product_area_filter_uses_portfolio_field_and_reflects_updates(client):
    """Filter by the portfolio's own area regardless of linked applications."""
    http, db = client
    headers = admin(http)
    payments = db.portfolios.find_one({'name': 'Payments'})
    payments_id = str(payments['_id'])
    # A matching application in a differently classified portfolio must not match.
    db.applications.update_one({'name': 'Customer Profile'}, {'$set': {'productArea': 'Portfolio Area'}})
    db.applications.update_one({'name': 'Billing'}, {'$set': {'productArea': ''}})
    body = {'name': 'Payments', 'owner': 'Payments Team', 'description': 'Payments portfolio', 'productArea': 'Portfolio Area', 'status': 'Active'}
    changed = http.put(f'/api/portfolios/{payments_id}', headers=headers, json=body)
    assert changed.status_code == 200
    assert {key: changed.json()[key] for key in body} == body
    assert {key: db.portfolios.find_one({'_id': payments['_id']})[key] for key in body} == body
    detail = http.get(f'/api/portfolios/{payments_id}', headers=headers)
    assert detail.status_code == 200
    assert {key: detail.json()[key] for key in body} == body
    assert set(db.applications.distinct('productArea', {'portfolioId': payments_id})) == {'Payments', ''}

    empty_body = {'name': 'Unlinked', 'owner': 'Team', 'description': '', 'productArea': 'Portfolio Area', 'status': 'Active'}
    created = http.post('/api/portfolios', headers=headers, json=empty_body)
    assert created.status_code == 201
    empty_id = created.json()['id']
    assert db.applications.count_documents({'portfolioId': empty_id}) == 0

    result = http.get('/api/portfolios', headers=headers, params={'productArea': 'Portfolio Area'})
    assert result.status_code == 200
    assert result.json()['total'] == 2
    assert [item['id'] for item in result.json()['items']] == [payments_id, empty_id]
    assert [(item['applicationCount'], item['qualityScore']) for item in result.json()['items']] == [(3, 76), (0, None)]
    paged = http.get('/api/portfolios', headers=headers, params={'productArea': 'Portfolio Area', 'limit': 1, 'offset': 1})
    assert paged.status_code == 200
    assert paged.json()['total'] == 2
    assert [item['id'] for item in paged.json()['items']] == [empty_id]

    updated_body = {**body, 'productArea': 'Changed Area'}
    updated = http.put(f'/api/portfolios/{payments_id}', headers=headers, json=updated_body)
    assert updated.status_code == 200
    assert {key: updated.json()[key] for key in updated_body} == updated_body
    stored = db.portfolios.find_one({'_id': payments['_id']})
    assert {key: stored[key] for key in updated_body} == updated_body
    reread = http.get(f'/api/portfolios/{payments_id}', headers=headers)
    assert reread.status_code == 200
    assert {key: reread.json()[key] for key in updated_body} == updated_body
    old_area = http.get('/api/portfolios', headers=headers, params={'productArea': 'Portfolio Area'})
    new_area = http.get('/api/portfolios', headers=headers, params={'productArea': 'Changed Area'})
    assert old_area.status_code == new_area.status_code == 200
    assert old_area.json()['total'] == 1
    assert [item['id'] for item in old_area.json()['items']] == [empty_id]
    assert new_area.json()['total'] == 1
    assert [item['id'] for item in new_area.json()['items']] == [payments_id]


def test_auth_validation_mutations_and_audit(client):
    """Require initial password change, validate and audit writes and references."""
    http, db = client
    assert http.get('/api/portfolios').status_code == 401
    restricted = login(http)
    assert http.get('/api/portfolios', headers=restricted).status_code == 403
    headers = admin(http)
    assert http.get('/api/portfolios?limit=101', headers=headers).status_code == 422
    first_response = http.get('/api/portfolios?limit=1&offset=0', headers=headers)
    second_response = http.get('/api/portfolios?limit=1&offset=1', headers=headers)
    assert first_response.status_code == second_response.status_code == 200
    first = first_response.json()
    second = second_response.json()
    assert first['total'] == 3 and second['total'] == 3 and first['items'][0]['id'] != second['items'][0]['id']
    assert http.get('/api/portfolios?status=Invalid', headers=headers).status_code == 422
    assert http.get('/api/portfolios/not-an-id', headers=headers).status_code == 404
    assert http.post('/api/portfolios', headers=headers, json={'name': '   '}).status_code == 422
    body = {'name': 'New Portfolio', 'owner': 'Owner', 'description': 'Details', 'productArea': 'Area', 'status': 'Active'}
    created = http.post('/api/portfolios', headers=headers, json=body)
    assert created.status_code == 201 and created.json()['qualityScore'] is None
    ident = created.json()['id']
    assert http.post('/api/portfolios', headers=headers, json=body).status_code == 409
    updated = http.put(f'/api/portfolios/{ident}', headers=headers, json={**body, 'status': 'Archived'})
    assert updated.status_code == 200
    assert updated.json()['status'] == 'Archived'
    linked = http.get(f'/api/portfolios/{ident}/applications', headers=headers)
    assert linked.status_code == 200
    assert linked.json() == {'items': [], 'total': 0}
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
    detail = http.get(f'/api/portfolios/{ident}', headers=leader)
    assert detail.status_code == 200
    assert detail.json()['canEdit'] is False
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


def test_invalid_portfolio_writes_preserve_record_and_audit(client):
    """Invalid create and update payloads do not write or audit anything."""
    http, db = client
    headers = admin(http)
    original = db.portfolios.find_one({'name': 'Payments'})
    body = {'name': 'New Portfolio', 'owner': 'Owner', 'description': 'Details', 'productArea': 'Area', 'status': 'Active'}
    invalid = [{key: value for key, value in body.items() if key != 'name'}]
    invalid += [{**body, field: value} for field, value in [
        ('name', None), ('name', ' '), ('name', 'x' * 201),
        ('owner', 'x' * 201), ('description', 'x' * 4001),
        ('productArea', 'x' * 201), ('status', 'Invalid'), ('owner', {}),
        ('unexpected', True),
    ]]
    count = db.portfolios.count_documents({})
    events = db.audit_logs.count_documents({'entityType': 'portfolio'})
    for bad in invalid:
        created = http.post('/api/portfolios', headers=headers, json=bad)
        assert created.status_code == 422, (bad, created.text)
        changed = http.put(f"/api/portfolios/{original['_id']}", headers=headers, json=bad)
        assert changed.status_code == 422, (bad, changed.text)
        assert db.portfolios.find_one({'_id': original['_id']}) == original
        assert db.portfolios.count_documents({}) == count
        assert db.audit_logs.count_documents({'entityType': 'portfolio'}) == events


def test_portfolio_text_is_json_and_encoded_traversal_cannot_read_other_scope(client):
    """Persist hostile text as data and avoid disclosure via encoded paths."""
    http, db = client
    headers = login(http, 'leader')
    payments = db.portfolios.find_one({'name': 'Payments'})
    outside = db.portfolios.find_one({'name': 'Customer Platform'})
    hostile = '<img src=x onerror=alert("portfolio-xss")>'
    body = {'name': payments['name'], 'owner': payments['owner'], 'description': hostile, 'productArea': payments['productArea'], 'status': payments['status']}
    changed = http.put(f"/api/portfolios/{payments['_id']}", headers=headers, json=body)
    assert changed.status_code == 200
    detail = http.get(f"/api/portfolios/{payments['_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.headers['content-type'].startswith('application/json')
    assert detail.json()['description'] == hostile
    assert db.portfolios.find_one({'_id': payments['_id']})['description'] == hostile
    for path in (f"/api/portfolios/%2e%2e%2fportfolios%2f{outside['_id']}",
                 f"/api/portfolios/%252e%252e%252fportfolios%252f{outside['_id']}"):
        response = http.get(path, headers=headers)
        assert response.status_code == 404
        assert outside['name'] not in response.text
        assert str(outside['_id']) not in response.text
