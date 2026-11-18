"""Exercise auth transactions against a disposable real MongoDB replica set."""

import asyncio
import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
import pytest
import pytest_asyncio

from app.audit.repository import AuditRepository
from app.auth.repository import UserRepository
from app.auth.service import change_landing, change_password, login, logout
from app.core.security import hash_password
from app.core.config import Settings
from app.core import seed
from app.main import create_app
from fastapi.testclient import TestClient
from pymongo import MongoClient

SECRET = 'test-signing-key-01234567890123456789'
PASSWORD = 'Initial-Password-123!'


@pytest_asyncio.fixture
async def db():
    """Allocate and remove only a uniquely named database on the test replica."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'auth_atomicity_' + uuid4().hex
    try:
        hello = await client.admin.command('hello')
        assert hello.get('setName'), 'Test MongoDB must be a replica set'
        yield client[name]
    finally:
        await client.drop_database(name)
        await client.close()


async def create_user(db):
    """Insert an active account for the transaction checks."""
    user = {'_id': ObjectId(), 'name': 'Engineer', 'email': 'engineer@example.com',
            'role': 'Engineer', 'status': 'Active', 'passwordHash': hash_password(PASSWORD),
            'tokenVersion': 0, 'mustChangePassword': False, 'defaultLandingPage': 'applications',
            'lastLogin': None, 'portfolioAccess': []}
    await db.users.insert_one(user.copy())
    return user


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['login', 'failed_login', 'unknown_login', 'password', 'landing', 'logout'])
async def test_audit_failure_rolls_back_account_and_event(db, monkeypatch, action):
    """An exception after audit insertion aborts all writes and returns no success."""
    user = await create_user(db)
    users, audit = UserRepository(db), AuditRepository(db)
    before = await db.users.find_one({'_id': user['_id']})
    original = audit.insert

    async def fail_after_insert(event, session=None):
        await original(event, session=session)
        raise RuntimeError('injected audit failure')

    monkeypatch.setattr(audit, 'insert', fail_after_insert)
    if action == 'login':
        call = login(users, audit, user['email'], PASSWORD, SECRET)
    elif action == 'failed_login':
        call = login(users, audit, user['email'], 'wrong', SECRET)
    elif action == 'unknown_login':
        call = login(users, audit, 'missing@example.com', 'wrong', SECRET)
    elif action == 'password':
        call = change_password(users, audit, user, PASSWORD, 'Replacement-Password123!', SECRET)
    elif action == 'landing':
        call = change_landing(users, audit, user, 'dashboard')
    else:
        call = logout(users, audit, user)
    with pytest.raises(RuntimeError, match='injected audit failure'):
        await call
    assert await db.users.find_one({'_id': user['_id']}) == before
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['password', 'landing', 'logout'])
@pytest.mark.parametrize('change', [{'status': 'Inactive'}, {'tokenVersion': 1}, {'role': 'Auditor'}, {'passwordHash': 'reset'}])
async def test_stale_identity_cannot_mutate(db, action, change):
    """A request authenticated before a security change cannot mutate afterwards."""
    user = await create_user(db)
    await db.users.update_one({'_id': user['_id']}, {'$set': change})
    before = await db.users.find_one({'_id': user['_id']})
    users, audit = UserRepository(db), AuditRepository(db)
    if action == 'password':
        call = change_password(users, audit, user, PASSWORD, 'Replacement-Password123!', SECRET)
    elif action == 'landing':
        call = change_landing(users, audit, user, 'dashboard')
    else:
        call = logout(users, audit, user)
    with pytest.raises(HTTPException) as error:
        await call
    assert error.value.status_code == 401
    assert await db.users.find_one({'_id': user['_id']}) == before
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_concurrent_logout_cannot_both_succeed(db):
    """Concurrent sign-outs serialize on the token version and commit one event."""
    user = await create_user(db)
    async def perform():
        try:
            await logout(UserRepository(db), AuditRepository(db), user)
            return 204
        except HTTPException as exc:
            return exc.status_code
    assert sorted(await asyncio.gather(perform(), perform())) == [204, 401]
    assert (await db.users.find_one({'_id': user['_id']}))['tokenVersion'] == 1
    assert await db.audit_logs.count_documents({'details': 'Signed out'}) == 1


@pytest.mark.asyncio
async def test_failed_login_is_committed_before_401(db):
    """A failed login records its audit event despite the error response."""
    user = await create_user(db)
    with pytest.raises(HTTPException) as error:
        await login(UserRepository(db), AuditRepository(db), user['email'], 'wrong', SECRET)
    assert error.value.status_code == 401
    assert await db.audit_logs.count_documents({'details': 'Login failed'}) == 1


@pytest.mark.asyncio
async def test_seed_rolls_back_if_portfolio_guard_fails(db, monkeypatch):
    """A failed seed guard leaves no partially initialized documents."""
    settings = Settings(environment='test', jwt_secret=SECRET, seed_on_startup=True,
                        seed_admin_password=PASSWORD, seed_demo_password=PASSWORD)
    original = seed.guard_portfolio

    async def fail_after_guard(database, key, session):
        await original(database, key, session)
        raise RuntimeError('injected seed failure')

    monkeypatch.setattr(seed, 'guard_portfolio', fail_after_guard)
    with pytest.raises(RuntimeError, match='injected seed failure'):
        await seed.seed_database(db, settings)
    for collection in ('users', 'roles', 'portfolios', 'applications'):
        assert await db[collection].count_documents({}) == 0


def test_startup_rejects_standalone_before_index_or_seed():
    """An unsupported server is rejected before any application data is created."""
    uri = os.environ.get('TEST_MONGODB_STANDALONE_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_STANDALONE_URI to a disposable standalone')
    name = 'auth_startup_' + uuid4().hex
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    settings = Settings(environment='test', jwt_secret=SECRET, mongodb_uri=uri,
                        mongodb_db=name, seed_on_startup=True,
                        seed_admin_password=PASSWORD, seed_demo_password=PASSWORD)
    try:
        hello = client.admin.command('hello')
        assert not hello.get('setName') and hello.get('msg') != 'isdbgrid'
        with pytest.raises(RuntimeError, match='must support transactions'):
            with TestClient(create_app(settings)):
                pass
        assert name not in client.list_database_names()
    finally:
        client.close()
