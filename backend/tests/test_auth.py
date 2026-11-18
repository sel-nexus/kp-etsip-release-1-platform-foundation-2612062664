"""Exercise authentication against a disposable real MongoDB database."""

from uuid import uuid4
from jose import jwt
from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest
from app.core.config import Settings
from app.main import create_app

SECRET = "test-signing-key-01234567890123456789"
ADMIN_PASSWORD = "Initial-Admin-Password123!"
DEMO_PASSWORD = "Initial-Demo-Password123!"


@pytest.fixture
def client():
    """Start the app on a unique database and remove only that database afterward."""
    name = "ketsip_auth_test_" + uuid4().hex
    settings = Settings(environment="test", jwt_secret=SECRET, mongodb_uri="mongodb://127.0.0.1:27017", mongodb_db=name, seed_on_startup=True, seed_admin_password=ADMIN_PASSWORD, seed_demo_password=DEMO_PASSWORD)
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


def test_profile_protected_and_excludes_password_hash(client):
    """Require an active database-backed session and return only public fields."""
    http, db = client
    assert http.get('/api/auth/me').status_code == 401
    assert http.get('/api/auth/me', headers=auth('bad-token')).status_code == 401
    token = signin(http).json()['token']
    response = http.get('/api/auth/me', headers=auth(token))
    assert response.status_code == 200 and response.json()['email'] == 'admin@ketsip.local'
    assert 'passwordHash' not in response.json()
    db.users.update_one({'email': 'admin@ketsip.local'}, {'$set': {'status': 'Inactive'}})
    assert http.get('/api/auth/me', headers=auth(token)).status_code == 401
    assert signin(http).status_code == 401


def test_initial_admin_password_change_is_enforced(client):
    """Deny preference changes until the initial password is replaced."""
    http, _ = client
    token = signin(http).json()['token']
    assert http.put('/api/auth/landing', headers=auth(token), json={'defaultLandingPage': 'applications'}).status_code == 403
    assert http.post('/api/auth/password', headers=auth(token), json={'currentPassword': 'wrong', 'newPassword': 'New-Password-123456!'}).status_code == 400
    changed = http.post('/api/auth/password', headers=auth(token), json={'currentPassword': ADMIN_PASSWORD, 'newPassword': 'New-Password-123456!'})
    assert changed.status_code == 200 and changed.json()['user']['mustChangePassword'] is False
    assert http.get('/api/auth/me', headers=auth(token)).status_code == 401
    assert http.put('/api/auth/landing', headers=auth(changed.json()['token']), json={'defaultLandingPage': 'applications'}).status_code == 200


def test_password_and_landing_persist_across_sessions(client):
    """Persist changes, reject the old credential, and invalidate logout tokens."""
    http, db = client
    token = signin(http, 'engineer@ketsip.local', DEMO_PASSWORD).json()['token']
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


def test_production_rejects_known_credentials():
    """Fail configuration validation for known development signing keys."""
    with pytest.raises(ValueError):
        Settings(environment='production', jwt_secret='dev-only-ketsip-signing-secret-not-for-production')
    with pytest.raises(ValueError):
        Settings(environment='production', jwt_secret='x' * 40, seed_on_startup=True, seed_admin_password='DevAdmin-Change123!', seed_demo_password='DevDemo-Change123!')
