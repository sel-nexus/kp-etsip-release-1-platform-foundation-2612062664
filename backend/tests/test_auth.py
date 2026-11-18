"""Exercise authentication against a disposable real MongoDB database."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4
from jose import jwt
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.audit.repository import AuditRepository
from app.auth.repository import UserRepository
from app.auth.service import change_landing, change_password, login
from app.core.config import Settings
from app.main import create_app

SECRET = "test-signing-key-01234567890123456789"
ADMIN_PASSWORD = "Initial-Admin-Password123!"
DEMO_PASSWORD = "Initial-Demo-Password123!"


@pytest.fixture
def client():
    """Start the app on a unique database and remove only that database afterward."""
    name = "ketsip_auth_test_" + uuid4().hex
    settings = Settings(environment="test", jwt_secret=SECRET, mongodb_uri=os.environ.get("TEST_MONGODB_REPLICA_URI", "mongodb://127.0.0.1:27017/?directConnection=true"), mongodb_db=name, seed_on_startup=True, seed_admin_password=ADMIN_PASSWORD, seed_demo_password=DEMO_PASSWORD)
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            yield http, mongo[name]
    finally:
        mongo.drop_database(name)
        mongo.close()


def signin(http, email="admin@ketsip.local", password=ADMIN_PASSWORD):
    """Request a real login session."""
    return http.post('/api/auth/login', json={"email": email, "password": password})


def signin_token(http, email="admin@ketsip.local", password=ADMIN_PASSWORD):
    """Require a successful login before extracting its token."""
    response = signin(http, email, password)
    assert response.status_code == 200, response.text
    return response.json()['token']


def auth(token):
    """Build a bearer authorization header."""
    return {"Authorization": f"Bearer {token}"}


def test_valid_credentials_issue_eight_hour_token_and_audit(client):
    """Issue a signed role-aware token and record success."""
    http, db = client
    response = signin(http)
    assert response.status_code == 200
    claims = jwt.decode(response.json()['token'], SECRET, algorithms=['HS256'])
    assert claims['exp'] - claims['iat'] == 8 * 60 * 60
    assert claims['role'] == 'Administrator' and claims['userId'] == response.json()['user']['id']
    assert db.audit_logs.count_documents({'action': 'login', 'details': 'Login succeeded'}) == 1
    assert db.users.find_one({'email': 'admin@ketsip.local'})['lastLogin'] is not None


def test_invalid_credentials_rejected_and_audited(client):
    """Record known and unknown failed sign-ins without passwords."""
    http, db = client
    assert signin(http, password='wrong').status_code == 401
    assert signin(http, email='nobody@ketsip.local', password='wrong').status_code == 401
    unknown = db.audit_logs.find_one({'userName': 'nobody@ketsip.local'})
    assert unknown['userId'] is None and unknown['details'] == 'Login failed'
    assert db.audit_logs.count_documents({'details': 'Login failed'}) == 2
    assert 'wrong' not in str(list(db.audit_logs.find()))
    assert http.post('/api/auth/login', json={'email': {'$ne': ''}, 'password': 'x'}).status_code == 422


def test_login_rejects_malformed_credentials_without_changing_state(client):
    """Reject invalid request bodies before modifying accounts or audit history."""
    http, db = client
    users_before = list(db.users.find().sort('_id'))
    audit_before = list(db.audit_logs.find().sort('_id'))
    cases = [
        ({'password': ADMIN_PASSWORD}, 'email'),
        ({'email': 'admin@ketsip.local'}, 'password'),
        ({'email': None, 'password': ADMIN_PASSWORD}, 'email'),
        ({'email': 'admin@ketsip.local', 'password': None}, 'password'),
        ({'email': 123, 'password': ADMIN_PASSWORD}, 'email'),
        ({'email': 'admin@ketsip.local', 'password': ['wrong']}, 'password'),
        ({'email': '', 'password': ADMIN_PASSWORD}, 'email'),
        ({'email': 'admin@ketsip.local', 'password': ''}, 'password'),
        ({'email': 'x' * 255, 'password': ADMIN_PASSWORD}, 'email'),
        ({'email': 'admin@ketsip.local', 'password': 'x' * 257}, 'password'),
    ]
    for body, field in cases:
        response = http.post('/api/auth/login', json=body)
        assert response.status_code == 422
        assert any(error['loc'] == ['body', field] for error in response.json()['detail'])
        assert list(db.users.find().sort('_id')) == users_before
        assert list(db.audit_logs.find().sort('_id')) == audit_before

    assert signin(http).status_code == 200


def test_password_change_rejects_invalid_schema_without_writes_or_audit(client):
    """Reject malformed password fields before any account or audit mutation."""
    http, db = client
    signed_in = signin(http, 'engineer@ketsip.local', DEMO_PASSWORD)
    assert signed_in.status_code == 200
    headers = auth(signed_in.json()['token'])
    users_before = list(db.users.find().sort('_id'))
    audit_before = list(db.audit_logs.find().sort('_id'))
    valid_new = 'Replacement-Password123!'
    cases = [
        ({'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': DEMO_PASSWORD}, 'newPassword'),
        ({'currentPassword': None, 'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': None}, 'newPassword'),
        ({'currentPassword': {'$ne': ''}, 'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': 123, 'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': ['replacement']}, 'newPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': False}, 'newPassword'),
        ({'currentPassword': '', 'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': 'x' * 257, 'newPassword': valid_new}, 'currentPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': ''}, 'newPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': 'x' * 11}, 'newPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': 'x' * 73}, 'newPassword'),
        ({'currentPassword': DEMO_PASSWORD, 'newPassword': 'é' * 37}, 'newPassword'),
    ]
    for body, field in cases:
        response = http.post('/api/auth/password', headers=headers, json=body)
        assert response.status_code == 422, body
        assert any(error['loc'] == ['body', field] for error in response.json()['detail'])
        assert list(db.users.find().sort('_id')) == users_before
        assert list(db.audit_logs.find().sort('_id')) == audit_before


def test_logout_rejects_unauthorized_requests_without_changing_sessions(client):
    """Reject missing, invalid and expired tokens without invalidating a valid session."""
    http, db = client
    signed_in = signin(http, 'engineer@ketsip.local', DEMO_PASSWORD)
    assert signed_in.status_code == 200
    token = signed_in.json()['token']
    claims = jwt.decode(token, SECRET, algorithms=['HS256'])
    now = datetime.now(timezone.utc)
    claims['iat'] = int((now - timedelta(hours=2)).timestamp())
    claims['exp'] = int((now - timedelta(hours=1)).timestamp())
    expired = jwt.encode(claims, SECRET, algorithm='HS256')
    users_before = list(db.users.find().sort('_id'))
    audit_before = list(db.audit_logs.find().sort('_id'))

    for headers in [{}, auth('bad-token'), auth(expired)]:
        response = http.post('/api/auth/logout', headers=headers)
        assert response.status_code == 401
        assert response.json() == {'detail': 'Invalid or expired session'}
        assert list(db.users.find().sort('_id')) == users_before
        assert list(db.audit_logs.find().sort('_id')) == audit_before
        assert http.get('/api/auth/me', headers=auth(token)).status_code == 200

    assert http.post('/api/auth/logout', headers=auth(token)).status_code == 204
    users_after_logout = list(db.users.find().sort('_id'))
    audit_after_logout = list(db.audit_logs.find().sort('_id'))
    assert http.post('/api/auth/logout', headers=auth(token)).status_code == 401
    assert list(db.users.find().sort('_id')) == users_after_logout
    assert list(db.audit_logs.find().sort('_id')) == audit_after_logout


def test_profile_protected_and_excludes_password_hash(client):
    """Require an active database-backed session and return only public fields."""
    http, db = client
    assert http.get('/api/auth/me').status_code == 401
    assert http.get('/api/auth/me', headers=auth('bad-token')).status_code == 401
    token = signin_token(http)
    response = http.get('/api/auth/me', headers=auth(token))
    assert response.status_code == 200 and response.json()['email'] == 'admin@ketsip.local'
    assert 'passwordHash' not in response.json()
    db.users.update_one({'email': 'admin@ketsip.local'}, {'$set': {'status': 'Inactive'}})
    assert http.get('/api/auth/me', headers=auth(token)).status_code == 401
    assert signin(http).status_code == 401


def test_expired_signed_token_cannot_read_or_mutate_profile(client):
    """Reject expired sessions without exposing or changing account data."""
    http, db = client
    signed_in = signin(http, 'engineer@ketsip.local', DEMO_PASSWORD)
    assert signed_in.status_code == 200
    claims = jwt.decode(signed_in.json()['token'], SECRET, algorithms=['HS256'])
    now = datetime.now(timezone.utc)
    claims['iat'] = int((now - timedelta(hours=2)).timestamp())
    claims['exp'] = int((now - timedelta(hours=1)).timestamp())
    expired = jwt.encode(claims, SECRET, algorithm='HS256')
    assert jwt.decode(expired, SECRET, algorithms=['HS256'], options={'verify_exp': False}) == claims

    before = db.users.find_one({'email': 'engineer@ketsip.local'})
    audit_before = list(db.audit_logs.find())
    assert before['defaultLandingPage'] == 'applications'
    read = http.get('/api/auth/me', headers=auth(expired))
    assert read.status_code == 401
    assert read.json() == {'detail': 'Invalid or expired session'}
    mutation = http.put('/api/auth/landing', headers=auth(expired), json={'defaultLandingPage': 'dashboard'})
    assert mutation.status_code == 401
    assert mutation.json() == {'detail': 'Invalid or expired session'}
    assert db.users.find_one({'_id': before['_id']}) == before
    assert list(db.audit_logs.find()) == audit_before

    claims['exp'] = int((now + timedelta(hours=1)).timestamp())
    valid = jwt.encode(claims, SECRET, algorithm='HS256')
    profile = http.get('/api/auth/me', headers=auth(valid))
    assert profile.status_code == 200
    assert profile.json()['email'] == before['email']
    assert http.put('/api/auth/landing', headers=auth(valid), json={'defaultLandingPage': 'dashboard'}).status_code == 200
    assert db.users.find_one({'_id': before['_id']})['defaultLandingPage'] == 'dashboard'


def test_auth_service_login_branches_with_real_mongodb(client):
    """Exercise successful, unknown, incorrect, and inactive account logins."""
    http, db = client
    users = UserRepository(http.app.state.db)
    audit = AuditRepository(http.app.state.db)
    result = http.portal.call(login, users, audit, ' ENGINEER@KETSIP.LOCAL ', DEMO_PASSWORD, SECRET)
    assert result['user']['email'] == 'engineer@ketsip.local'
    assert jwt.decode(result['token'], SECRET, algorithms=['HS256'])['sub'] == result['user']['id']
    assert db.users.find_one({'email': 'engineer@ketsip.local'})['lastLogin'] is not None

    for email, password in [('nobody@ketsip.local', 'wrong'), ('engineer@ketsip.local', 'wrong')]:
        with pytest.raises(HTTPException) as error:
            http.portal.call(login, users, audit, email, password, SECRET)
        assert error.value.status_code == 401
    db.users.update_one({'email': 'engineer@ketsip.local'}, {'$set': {'status': 'Inactive'}})
    with pytest.raises(HTTPException) as error:
        http.portal.call(login, users, audit, 'engineer@ketsip.local', DEMO_PASSWORD, SECRET)
    assert error.value.status_code == 401
    assert db.audit_logs.count_documents({'details': 'Login succeeded'}) == 1
    assert db.audit_logs.count_documents({'details': 'Login failed'}) == 3
    assert db.audit_logs.find_one({'userName': 'nobody@ketsip.local'})['userId'] is None


def test_auth_service_password_and_landing_branches_with_real_mongodb(client):
    """Reject invalid password changes and persist successful service changes."""
    http, db = client
    users = UserRepository(http.app.state.db)
    audit = AuditRepository(http.app.state.db)
    user = http.portal.call(users.by_email, 'engineer@ketsip.local')
    before = db.users.find_one({'_id': user['_id']})
    for current, new, detail in [
        ('wrong', 'Changed-Password-123!', 'Current password is incorrect'),
        (DEMO_PASSWORD, DEMO_PASSWORD, 'Choose a different password'),
    ]:
        with pytest.raises(HTTPException) as error:
            http.portal.call(change_password, users, audit, user, current, new, SECRET)
        assert error.value.status_code == 400 and error.value.detail == detail
        assert db.users.find_one({'_id': user['_id']}) == before
    assert db.audit_logs.count_documents({}) == 0

    changed = http.portal.call(change_password, users, audit, user, DEMO_PASSWORD, 'Changed-Password-123!', SECRET)
    saved = db.users.find_one({'_id': user['_id']})
    assert saved['passwordHash'] != before['passwordHash']
    assert saved['tokenVersion'] == before['tokenVersion'] + 1
    assert jwt.decode(changed['token'], SECRET, algorithms=['HS256'])['ver'] == saved['tokenVersion']
    assert signin(http, 'engineer@ketsip.local', DEMO_PASSWORD).status_code == 401
    assert signin(http, 'engineer@ketsip.local', 'Changed-Password-123!').status_code == 200
    landed = http.portal.call(change_landing, users, audit, saved, 'dashboard')
    assert landed['defaultLandingPage'] == 'dashboard'
    assert db.users.find_one({'_id': user['_id']})['defaultLandingPage'] == 'dashboard'
    assert db.audit_logs.count_documents({'details': 'Password changed'}) == 1
    assert db.audit_logs.count_documents({'details': 'Landing preference changed'}) == 1


def test_initial_admin_password_change_is_enforced(client):
    """Deny preference changes until the initial password is replaced."""
    http, _ = client
    token = signin_token(http)
    assert http.put('/api/auth/landing', headers=auth(token), json={'defaultLandingPage': 'applications'}).status_code == 403
    assert http.post('/api/auth/password', headers=auth(token), json={'currentPassword': 'wrong', 'newPassword': 'New-Password-123456!'}).status_code == 400
    changed = http.post('/api/auth/password', headers=auth(token), json={'currentPassword': ADMIN_PASSWORD, 'newPassword': 'New-Password-123456!'})
    assert changed.status_code == 200 and changed.json()['user']['mustChangePassword'] is False
    assert http.get('/api/auth/me', headers=auth(token)).status_code == 401
    assert http.put('/api/auth/landing', headers=auth(changed.json()['token']), json={'defaultLandingPage': 'applications'}).status_code == 200


def test_password_and_landing_persist_across_sessions(client):
    """Persist changes, reject the old credential, and invalidate logout tokens."""
    http, db = client
    token = signin_token(http, 'engineer@ketsip.local', DEMO_PASSWORD)
    assert http.put('/api/auth/landing', headers=auth(token), json={'defaultLandingPage': 'other'}).status_code == 422
    assert http.put('/api/auth/landing', headers=auth(token), json={'defaultLandingPage': 'dashboard'}).status_code == 200
    assert http.post('/api/auth/password', headers=auth(token), json={'currentPassword': DEMO_PASSWORD, 'newPassword': 'short'}).status_code == 422
    assert http.post('/api/auth/password', headers=auth(token), json={'currentPassword': DEMO_PASSWORD, 'newPassword': DEMO_PASSWORD}).status_code == 400
    changed = http.post('/api/auth/password', headers=auth(token), json={'currentPassword': DEMO_PASSWORD, 'newPassword': 'Changed-Password-123!'})
    assert changed.status_code == 200
    assert signin(http, 'engineer@ketsip.local', DEMO_PASSWORD).status_code == 401
    fresh = signin(http, 'engineer@ketsip.local', 'Changed-Password-123!')
    assert fresh.status_code == 200 and fresh.json()['user']['defaultLandingPage'] == 'dashboard'
    assert db.users.find_one({'email': 'engineer@ketsip.local'})['defaultLandingPage'] == 'dashboard'
    new_token = fresh.json()['token']
    assert http.post('/api/auth/logout', headers=auth(new_token)).status_code == 204
    assert http.get('/api/auth/me', headers=auth(new_token)).status_code == 401
    assert db.audit_logs.count_documents({'action': 'edit', 'details': 'Password changed'}) == 1


@pytest.mark.parametrize('body', [
    pytest.param({}, id='missing'),
    pytest.param({'defaultLandingPage': None}, id='null'),
    pytest.param({'defaultLandingPage': 1}, id='number'),
    pytest.param({'defaultLandingPage': True}, id='boolean'),
    pytest.param({'defaultLandingPage': []}, id='array'),
    pytest.param({'defaultLandingPage': {'$ne': ''}}, id='operator-object'),
    pytest.param({'defaultLandingPage': ''}, id='empty'),
    pytest.param({'defaultLandingPage': 'other'}, id='unsupported'),
    pytest.param({'defaultLandingPage': 'x' * 10000}, id='oversized'),
])
def test_malformed_landing_preference_does_not_mutate_or_audit(client, body):
    """Each invalid landing value is rejected before persistence or audit."""
    http, db = client
    headers = auth(signin_token(http, 'engineer@ketsip.local', DEMO_PASSWORD))
    users_before = list(db.users.find().sort('_id'))
    audit_before = list(db.audit_logs.find().sort('_id'))

    response = http.put('/api/auth/landing', headers=headers, json=body)
    assert response.status_code == 422
    assert any(error['loc'] == ['body', 'defaultLandingPage'] for error in response.json()['detail'])
    assert list(db.users.find().sort('_id')) == users_before
    assert list(db.audit_logs.find().sort('_id')) == audit_before
    profile = http.get('/api/auth/me', headers=headers)
    assert profile.status_code == 200
    assert profile.json()['defaultLandingPage'] == 'applications'


def test_production_rejects_known_credentials():
    """Fail configuration validation for known development signing keys."""
    with pytest.raises(ValueError):
        Settings(environment='production', jwt_secret='dev-only-ketsip-signing-secret-not-for-production')
    with pytest.raises(ValueError):
        Settings(environment='production', jwt_secret='x' * 40, seed_on_startup=True, seed_admin_password='DevAdmin-Change123!', seed_demo_password='DevDemo-Change123!')
