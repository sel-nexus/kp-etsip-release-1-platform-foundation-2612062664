"""Cross-feature journeys against the real API and disposable MongoDB.

Run after structural review, from backend: pytest tests/test_integration.py -v.
A MongoDB replica set must be available at TEST_MONGODB_REPLICA_URI.
Each test owns a uniquely named database which is dropped on teardown.
"""

import csv
import io
import os
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def system():
    """Start the real seeded application and clean up only its unique database."""
    name = 'ketsip_integration_' + uuid4().hex
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true')
    settings = Settings(
        environment='test', jwt_secret='integration-signing-key-01234567890123456789',
        mongodb_uri=uri, mongodb_db=name, seed_on_startup=True,
        seed_admin_password='Initial-Admin-Password123!',
        seed_demo_password='Initial-Demo-Password123!',
    )
    mongo = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        try:
            mongo.drop_database(name)
        finally:
            mongo.close()


def login(http, role):
    """Authenticate through the API and return the issued bearer token."""
    password = 'Initial-Admin-Password123!' if role == 'admin' else 'Initial-Demo-Password123!'
    response = http.post('/api/auth/login', json={'email': f'{role}@ketsip.local', 'password': password})
    assert response.status_code == 200, response.text
    assert response.json()['user']['email'] == f'{role}@ketsip.local'
    return {'Authorization': 'Bearer ' + response.json()['token']}


def admin_login(http):
    """Complete the seeded administrator's required first password change."""
    pending = login(http, 'admin')
    response = http.post('/api/auth/password', headers=pending, json={
        'currentPassword': 'Initial-Admin-Password123!',
        'newPassword': 'Changed-Admin-Password123!',
    })
    assert response.status_code == 200, response.text
    assert response.json()['user']['mustChangePassword'] is False
    return {'Authorization': 'Bearer ' + response.json()['token']}


def portfolio_body(name):
    """Build a valid portfolio request."""
    return {'name': name, 'owner': 'Integration team', 'description': 'Test portfolio',
            'productArea': 'Integration', 'status': 'Active'}


def application_body(name, portfolio_id, quality=60):
    """Build a valid application request with known dashboard metrics."""
    return {
        'name': name, 'description': 'Integration application', 'portfolioId': portfolio_id,
        'productArea': 'Integration', 'businessOwner': 'Business', 'technologyOwner': 'Technology',
        'etsOwner': 'Quality', 'criticality': 'High', 'tier': 2, 'lifecycleStatus': 'Active',
        'technologyStack': 'Python', 'repositoryLink': None, 'pipelineLink': None,
        'applicationHealthScore': 80, 'qualityScore': quality, 'automationCoverage': 40,
        'unitTestCoverage': 50, 'openDefects': 3, 'openCriticalDefects': 2, 'riskLevel': 'High',
    }


def assert_no_orphan_applications(db):
    """Check every persisted application reference points to an existing portfolio."""
    portfolio_ids = {str(doc['_id']) for doc in db.portfolios.find({}, {'_id': 1})}
    references = {doc['portfolioId'] for doc in db.applications.find({}, {'portfolioId': 1})}
    assert references <= portfolio_ids


def test_inventory_lifecycle_flows_to_insights_reports_and_audit(system):
    """Create, edit and delete linked inventory; verify every downstream read model."""
    http, db = system
    headers = admin_login(http)
    actor = db.users.find_one({'email': 'admin@ketsip.local'})
    me = http.get('/api/auth/me', headers=headers)
    assert me.status_code == 200
    assert me.json()['id'] == str(actor['_id']) and 'passwordHash' not in me.json()

    portfolio = portfolio_body('Integration Portfolio')
    created_portfolio = http.post('/api/portfolios', headers=headers, json=portfolio)
    assert created_portfolio.status_code == 201, created_portfolio.text
    pid = created_portfolio.json()['id']
    assert created_portfolio.json()['applicationCount'] == 0
    assert created_portfolio.json()['qualityScore'] is None
    assert db.portfolios.find_one({'_id': ObjectId(pid)})['name'] == portfolio['name']

    edited_portfolio = {**portfolio, 'owner': 'Updated owner'}
    response = http.put(f'/api/portfolios/{pid}', headers=headers, json=edited_portfolio)
    assert response.status_code == 200
    assert response.json()['owner'] == 'Updated owner'
    assert db.portfolios.find_one({'_id': ObjectId(pid)})['owner'] == 'Updated owner'

    body = application_body('Integration App', pid)
    created = http.post('/api/applications', headers=headers, json=body)
    assert created.status_code == 201, created.text
    app_id = created.json()['id']
    assert created.json()['portfolioId'] == pid
    assert created.json()['portfolioName'] == portfolio['name']
    stored = db.applications.find_one({'_id': ObjectId(app_id)})
    assert {key: stored[key] for key in body} == body

    changed_body = {**body, 'name': 'Integration App Revised', 'qualityScore': 90,
                    'applicationHealthScore': 70, 'automationCoverage': 60,
                    'openCriticalDefects': 4}
    changed = http.put(f'/api/applications/{app_id}', headers=headers, json=changed_body)
    assert changed.status_code == 200
    assert changed.json()['qualityScore'] == 90 and changed.json()['name'] == changed_body['name']
    stored = db.applications.find_one({'_id': ObjectId(app_id)})
    assert {key: stored[key] for key in changed_body} == changed_body

    detail = http.get(f'/api/portfolios/{pid}', headers=headers)
    linked = http.get(f'/api/portfolios/{pid}/applications', headers=headers)
    assert detail.status_code == linked.status_code == 200
    assert detail.json()['applicationCount'] == 1 and detail.json()['qualityScore'] == 90
    assert linked.json()['total'] == 1
    assert linked.json()['items'] == [{'id': app_id, 'name': changed_body['name'],
                                       'productArea': 'Integration', 'qualityScore': 90, 'riskLevel': 'High'}]

    dashboard = http.get('/api/dashboard/executive', headers=headers, params={'portfolioId': pid})
    assert dashboard.status_code == 200
    metrics = dashboard.json()
    assert {key: metrics[key] for key in ('enterpriseQualityScore', 'applicationHealthScore',
            'automationMaturityScore', 'openCriticalDefects', 'applicationsAtRisk',
            'totalApplications', 'totalPortfolios')} == {
        'enterpriseQualityScore': 90, 'applicationHealthScore': 70, 'automationMaturityScore': 60,
        'openCriticalDefects': 4, 'applicationsAtRisk': 1, 'totalApplications': 1, 'totalPortfolios': 1}
    assert metrics['portfolios']['items'] == [{'id': pid, 'name': portfolio['name'],
        'applicationCount': 1, 'qualityScore': 90, 'applicationsAtRisk': 1}]

    apps = http.get('/api/reports/applications', headers=headers, params={'portfolioId': pid})
    portfolios = http.get('/api/reports/portfolios', headers=headers, params={'search': portfolio['name']})
    assert apps.status_code == portfolios.status_code == 200
    assert apps.json()['total'] == 1 and apps.json()['items'][0]['id'] == app_id
    assert apps.json()['items'][0]['qualityScore'] == 90
    assert portfolios.json()['total'] == 1 and portfolios.json()['items'][0]['applicationCount'] == 1
    assert portfolios.json()['items'][0]['qualityScore'] == 90
    export = http.get('/api/reports/applications', headers=headers,
                      params={'portfolioId': pid, 'format': 'csv'})
    assert export.status_code == 200 and export.headers['content-type'].startswith('text/csv')
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 1 and rows[0]['name'] == changed_body['name']
    assert rows[0]['portfolioName'] == portfolio['name'] and rows[0]['qualityScore'] == '90'

    before = db.portfolios.find_one({'_id': ObjectId(pid)})
    conflict = http.delete(f'/api/portfolios/{pid}', headers=headers)
    assert conflict.status_code == 409 and 'detail' in conflict.json()
    assert db.portfolios.find_one({'_id': ObjectId(pid)}) == before
    assert db.applications.find_one({'_id': ObjectId(app_id)}) == stored
    assert db.audit_logs.count_documents({'entityType': 'portfolio', 'entityId': pid, 'action': 'delete'}) == 0
    assert_no_orphan_applications(db)

    deleted = http.delete(f'/api/applications/{app_id}', headers=headers)
    assert deleted.status_code == 204
    assert db.applications.find_one({'_id': ObjectId(app_id)}) is None
    assert http.get(f'/api/applications/{app_id}', headers=headers).status_code == 404
    detail = http.get(f'/api/portfolios/{pid}', headers=headers)
    linked = http.get(f'/api/portfolios/{pid}/applications', headers=headers)
    assert detail.status_code == linked.status_code == 200
    assert detail.json()['applicationCount'] == 0 and detail.json()['qualityScore'] is None
    assert linked.json() == {'items': [], 'total': 0}
    dashboard = http.get('/api/dashboard/executive', headers=headers, params={'portfolioId': pid})
    assert dashboard.status_code == 200
    assert dashboard.json()['totalApplications'] == 0 and dashboard.json()['enterpriseQualityScore'] is None
    report = http.get('/api/reports/applications', headers=headers, params={'portfolioId': pid})
    assert report.status_code == 200 and report.json()['items'] == [] and report.json()['total'] == 0
    assert http.delete(f'/api/portfolios/{pid}', headers=headers).status_code == 204
    assert db.portfolios.find_one({'_id': ObjectId(pid)}) is None
    assert http.get(f'/api/portfolios/{pid}', headers=headers).status_code == 404
    assert db.applications.count_documents({'portfolioId': pid}) == 0
    assert_no_orphan_applications(db)

    audit = http.get('/api/audit-logs', headers=headers, params={'userId': str(actor['_id']), 'limit': 100})
    assert audit.status_code == 200
    events = audit.json()['items']
    for kind, ident in [('portfolio', pid), ('application', app_id)]:
        matching = [event for event in events if event['entityType'] == kind and event['entityId'] == ident]
        assert sorted(event['action'] for event in matching) == ['create', 'delete', 'edit']
        assert all(event['userId'] == str(actor['_id']) for event in matching)
        assert db.audit_logs.count_documents({'entityType': kind, 'entityId': ident, 'userId': actor['_id']}) == 3
    assert any(event['action'] == 'export' and event['entityType'] == 'application' for event in events)
    assert db.audit_logs.count_documents({'action': 'export', 'entityType': 'application', 'userId': actor['_id']}) == 1


def test_assigned_scope_rejects_missing_and_unauthorized_references_without_phantoms(system):
    """Keep both source and destination scope and failed reference writes intact."""
    http, db = system
    admin = admin_login(http)
    leader = login(http, 'leader')
    payments = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    outside = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    missing = str(ObjectId())
    actor = db.users.find_one({'email': 'leader@ketsip.local'})
    assert actor['portfolioAccess'] == [payments]
    me = http.get('/api/auth/me', headers=leader)
    assert me.status_code == 200 and me.json()['portfolioAccess'] == [payments]

    body = application_body('Leader Integration App', payments)
    created = http.post('/api/applications', headers=leader, json=body)
    assert created.status_code == 201, created.text
    app_id = created.json()['id']
    original = db.applications.find_one({'_id': ObjectId(app_id)})
    assert original['portfolioId'] == payments and original['qualityScore'] == 60
    outside_doc = db.applications.find_one({'name': 'Customer Profile'})
    outside_id = str(outside_doc['_id'])
    before_count = db.applications.count_documents({})
    before_audit = db.audit_logs.count_documents({'entityType': 'application'})

    missing_create = http.post('/api/applications', headers=admin,
                               json=application_body('Missing Reference App', missing))
    assert missing_create.status_code == 404 and 'detail' in missing_create.json()
    missing_edit = http.put(f'/api/applications/{app_id}', headers=admin,
                            json={**body, 'portfolioId': missing})
    assert missing_edit.status_code == 404 and 'detail' in missing_edit.json()
    forbidden_create = http.post('/api/applications', headers=leader,
                                 json=application_body('Forbidden Reference App', outside))
    assert forbidden_create.status_code == 403 and 'detail' in forbidden_create.json()
    forbidden_destination = http.put(f'/api/applications/{app_id}', headers=leader,
                                     json={**body, 'portfolioId': outside})
    assert forbidden_destination.status_code == 403
    forbidden_source = http.put(f'/api/applications/{outside_id}', headers=leader,
                                json=application_body('Unauthorized Source Edit', payments))
    assert forbidden_source.status_code == 403
    assert http.get(f'/api/applications/{outside_id}', headers=leader).status_code == 403
    assert http.get(f'/api/portfolios/{outside}/applications', headers=leader).status_code == 403
    assert db.applications.count_documents({}) == before_count
    assert db.applications.find_one({'_id': ObjectId(app_id)}) == original
    assert db.applications.find_one({'_id': outside_doc['_id']}) == outside_doc
    assert db.applications.count_documents({'name': {'$in': ['Missing Reference App', 'Forbidden Reference App', 'Unauthorized Source Edit']}}) == 0
    assert db.applications.count_documents({'portfolioId': missing}) == 0
    assert db.audit_logs.count_documents({'entityType': 'application'}) == before_audit
    assert_no_orphan_applications(db)

    linked = http.get(f'/api/portfolios/{payments}/applications', headers=leader)
    assert linked.status_code == 200 and any(item['id'] == app_id for item in linked.json()['items'])
    own_report = http.get('/api/reports/applications', headers=leader, params={'portfolioId': payments})
    outside_report = http.get('/api/reports/applications', headers=leader, params={'portfolioId': outside})
    assert own_report.status_code == outside_report.status_code == 200
    assert any(item['id'] == app_id for item in own_report.json()['items'])
    assert outside_report.json()['total'] == 0 and outside_report.json()['items'] == []
    outside_dashboard = http.get('/api/dashboard/executive', headers=leader, params={'portfolioId': outside})
    assert outside_dashboard.status_code == 200
    assert outside_dashboard.json()['totalPortfolios'] == 0 and outside_dashboard.json()['totalApplications'] == 0
    scoped_export = http.get('/api/reports/applications', headers=leader, params={'format': 'csv'})
    assert scoped_export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(scoped_export.text)))
    assert any(row['name'] == body['name'] for row in rows)
    assert all(row['portfolioName'] == 'Payments' for row in rows)
    assert not any(row['name'] == outside_doc['name'] for row in rows)
    assert db.audit_logs.count_documents({'userId': actor['_id'], 'action': 'export', 'entityType': 'application'}) == 1

    readonly = login(http, 'readonly')
    before_export = db.audit_logs.count_documents({'action': 'export'})
    denied = http.get('/api/reports/applications', headers=readonly, params={'format': 'csv'})
    assert denied.status_code == 403 and 'detail' in denied.json()
    assert db.audit_logs.count_documents({'action': 'export'}) == before_export


def test_moving_application_updates_both_portfolios_and_allows_old_portfolio_delete(system):
    """A valid move changes both aggregates and preserves referential integrity."""
    http, db = system
    headers = admin_login(http)
    ids = []
    for name in ('Integration Source', 'Integration Destination'):
        response = http.post('/api/portfolios', headers=headers, json=portfolio_body(name))
        assert response.status_code == 201, response.text
        ids.append(response.json()['id'])
    source, destination = ids
    body = application_body('Moving Integration App', source, quality=85)
    created = http.post('/api/applications', headers=headers, json=body)
    assert created.status_code == 201
    app_id = created.json()['id']
    moved = http.put(f'/api/applications/{app_id}', headers=headers, json={**body, 'portfolioId': destination})
    assert moved.status_code == 200
    assert moved.json()['portfolioId'] == destination and moved.json()['portfolioName'] == 'Integration Destination'
    assert db.applications.find_one({'_id': ObjectId(app_id)})['portfolioId'] == destination
    for pid, count, score in ((source, 0, None), (destination, 1, 85)):
        detail = http.get(f'/api/portfolios/{pid}', headers=headers)
        linked = http.get(f'/api/portfolios/{pid}/applications', headers=headers)
        assert detail.status_code == linked.status_code == 200
        assert detail.json()['applicationCount'] == count and detail.json()['qualityScore'] == score
        assert linked.json()['total'] == count
        assert [item['id'] for item in linked.json()['items']] == ([app_id] if count else [])
        dashboard = http.get('/api/dashboard/executive', headers=headers, params={'portfolioId': pid})
        assert dashboard.status_code == 200
        assert dashboard.json()['totalApplications'] == count and dashboard.json()['enterpriseQualityScore'] == score
    assert http.delete(f'/api/portfolios/{destination}', headers=headers).status_code == 409
    assert db.portfolios.find_one({'_id': ObjectId(destination)}) is not None
    assert http.delete(f'/api/portfolios/{source}', headers=headers).status_code == 204
    assert db.portfolios.find_one({'_id': ObjectId(source)}) is None
    assert db.applications.count_documents({'portfolioId': source}) == 0
    assert_no_orphan_applications(db)


def test_admin_assignment_and_permission_changes_flow_through_live_sessions_and_reports(system):
    """Propagate assignment and role changes through reads, exports and audit."""
    http, db = system
    admin = admin_login(http)
    existing_session = login(http, 'engineer')
    admin_id = db.users.find_one({'email': 'admin@ketsip.local'})['_id']
    engineer = db.users.find_one({'email': 'engineer@ketsip.local'})
    engineer_id = str(engineer['_id'])
    role = db.roles.find_one({'name': 'Engineer'})
    role_id = str(role['_id'])
    payments = str(db.portfolios.find_one({'name': 'Payments'})['_id'])
    customer = str(db.portfolios.find_one({'name': 'Customer Platform'})['_id'])
    payment_apps = list(db.applications.find({'portfolioId': payments}))
    customer_apps = list(db.applications.find({'portfolioId': customer}))
    assert len(payment_apps) == len(customer_apps) == 3
    assert engineer['portfolioAccess'] == [payments]
    assert {'view', 'create', 'edit', 'export'} <= set(role['permissions'])

    initial = http.get('/api/reports/applications', headers=existing_session)
    assert initial.status_code == 200
    assert {item['id'] for item in initial.json()['items']} == {str(doc['_id']) for doc in payment_apps}
    assert initial.json()['canCreate'] is True and initial.json()['canExport'] is True
    initial_dashboard = http.get('/api/dashboard/executive', headers=existing_session)
    assert initial_dashboard.status_code == 200
    assert initial_dashboard.json()['totalPortfolios'] == 1
    assert {item['id'] for item in initial_dashboard.json()['portfolios']['items']} == {payments}

    assignment = {'name': engineer['name'], 'email': engineer['email'], 'role': 'Engineer',
                  'portfolioAccess': [customer], 'status': 'Active'}
    updated = http.put(f'/api/users/{engineer_id}', headers=admin, json=assignment)
    assert updated.status_code == 200, updated.text
    assert updated.json()['portfolioAccess'] == [customer]
    assert db.users.find_one({'_id': engineer['_id']})['portfolioAccess'] == [customer]
    restricted = {'name': 'Engineer', 'description': 'Integration restricted', 'permissions': ['view']}
    changed_role = http.put(f'/api/roles/{role_id}', headers=admin, json=restricted)
    assert changed_role.status_code == 200, changed_role.text
    assert changed_role.json()['permissions'] == ['view']
    assert db.roles.find_one({'_id': role['_id']})['permissions'] == ['view']
    fresh_session = login(http, 'engineer')
    expected_ids = {str(doc['_id']) for doc in customer_apps}
    expected_names = {doc['name'] for doc in customer_apps}
    payment_names = {doc['name'] for doc in payment_apps}
    before_exports = db.audit_logs.count_documents({'userId': engineer['_id'], 'action': 'export'})

    for session in (existing_session, fresh_session):
        me = http.get('/api/auth/me', headers=session)
        assert me.status_code == 200 and me.json()['portfolioAccess'] == [customer]
        dashboard = http.get('/api/dashboard/executive', headers=session)
        assert dashboard.status_code == 200
        metrics = dashboard.json()
        assert metrics['totalPortfolios'] == 1 and metrics['totalApplications'] == 3
        assert metrics['enterpriseQualityScore'] == 79 and metrics['applicationHealthScore'] == 74
        assert metrics['automationMaturityScore'] == 64 and metrics['openCriticalDefects'] == 3
        assert metrics['applicationsAtRisk'] == 1
        assert {item['id'] for item in metrics['portfolios']['items']} == {customer}
        outside_dashboard = http.get('/api/dashboard/executive', headers=session, params={'portfolioId': payments})
        assert outside_dashboard.status_code == 200
        assert outside_dashboard.json()['totalPortfolios'] == 0
        assert outside_dashboard.json()['totalApplications'] == 0
        apps = http.get('/api/reports/applications', headers=session)
        assert apps.status_code == 200 and apps.json()['total'] == 3
        assert {item['id'] for item in apps.json()['items']} == expected_ids
        assert {item['name'] for item in apps.json()['items']} == expected_names
        assert apps.json()['canCreate'] is False and apps.json()['canExport'] is False
        assert all(item['portfolioId'] == customer and item['canEdit'] is False for item in apps.json()['items'])
        outside_apps = http.get('/api/reports/applications', headers=session, params={'portfolioId': payments})
        assert outside_apps.status_code == 200 and outside_apps.json()['items'] == []
        assert outside_apps.json()['total'] == 0
        portfolios = http.get('/api/reports/portfolios', headers=session)
        assert portfolios.status_code == 200 and portfolios.json()['total'] == 1
        assert {item['id'] for item in portfolios.json()['items']} == {customer}
        assert portfolios.json()['items'][0]['applicationCount'] == 3
        assert portfolios.json()['items'][0]['qualityScore'] == 79
        assert portfolios.json()['canExport'] is False
        outside_portfolio = http.get('/api/reports/portfolios', headers=session, params={'search': 'Payments'})
        assert outside_portfolio.status_code == 200 and outside_portfolio.json()['total'] == 0
        assert http.get(f"/api/applications/{payment_apps[0]['_id']}", headers=session).status_code == 403
        assert http.post('/api/applications', headers=session,
                         json=application_body('Denied Integration App', customer)).status_code == 403
        for kind in ('applications', 'portfolios'):
            denied = http.get(f'/api/reports/{kind}', headers=session, params={'format': 'csv'})
            assert denied.status_code == 403 and 'detail' in denied.json()
    assert db.applications.count_documents({'name': 'Denied Integration App'}) == 0
    assert db.audit_logs.count_documents({'userId': engineer['_id'], 'action': 'export'}) == before_exports

    restored = {'name': 'Engineer', 'description': 'Integration restored',
                'permissions': ['view', 'create', 'edit', 'export']}
    response = http.put(f'/api/roles/{role_id}', headers=admin, json=restored)
    assert response.status_code == 200, response.text
    assert response.json()['permissions'] == restored['permissions']
    assert db.roles.find_one({'_id': role['_id']})['permissions'] == restored['permissions']
    for session in (existing_session, fresh_session):
        apps = http.get('/api/reports/applications', headers=session)
        portfolios = http.get('/api/reports/portfolios', headers=session)
        assert apps.status_code == portfolios.status_code == 200
        assert {item['id'] for item in apps.json()['items']} == expected_ids
        assert apps.json()['canCreate'] is True and apps.json()['canExport'] is True
        assert all(item['canEdit'] is True for item in apps.json()['items'])
        assert {item['id'] for item in portfolios.json()['items']} == {customer}
        assert portfolios.json()['canExport'] is True
        for kind in ('applications', 'portfolios'):
            export = http.get(f'/api/reports/{kind}', headers=session, params={'format': 'csv'})
            assert export.status_code == 200 and export.headers['content-type'].startswith('text/csv')
            rows = list(csv.DictReader(io.StringIO(export.text)))
            if kind == 'applications':
                assert {row['name'] for row in rows} == expected_names
                assert len(rows) == 3 and all(row['portfolioName'] == 'Customer Platform' for row in rows)
                assert not ({row['name'] for row in rows} & payment_names)
            else:
                assert len(rows) == 1 and rows[0]['name'] == 'Customer Platform'
                assert rows[0]['applicationCount'] == '3' and rows[0]['qualityScore'] == '79'

    created = http.post('/api/applications', headers=existing_session,
                        json=application_body('Granted Integration App', customer, quality=88))
    assert created.status_code == 201, created.text
    created_id = created.json()['id']
    assert db.applications.find_one({'_id': ObjectId(created_id)})['portfolioId'] == customer
    observed = http.get('/api/reports/applications', headers=fresh_session)
    assert observed.status_code == 200 and observed.json()['total'] == 4
    assert {item['id'] for item in observed.json()['items']} == expected_ids | {created_id}
    assert_no_orphan_applications(db)

    audit = http.get('/api/audit-logs', headers=admin, params={'limit': 100})
    assert audit.status_code == 200
    events = audit.json()['items']
    for action, kind, ident, actor, count in (
        ('edit', 'user', engineer_id, admin_id, 1),
        ('edit', 'role', role_id, admin_id, 2),
        ('permissionChange', 'role', role_id, admin_id, 2),
        ('create', 'application', created_id, engineer['_id'], 1),
    ):
        query = {'action': action, 'entityType': kind, 'entityId': ident, 'userId': actor}
        assert db.audit_logs.count_documents(query) == count
        matching = [event for event in events if event['action'] == action and event['entityType'] == kind
                    and event['entityId'] == ident and event['userId'] == str(actor)]
        assert len(matching) == count
    for kind in ('application', 'portfolio'):
        query = {'action': 'export', 'entityType': kind, 'userId': engineer['_id']}
        assert db.audit_logs.count_documents(query) == 2
        assert len([event for event in events if event['action'] == 'export' and event['entityType'] == kind
                    and event['userId'] == engineer_id]) == 2
