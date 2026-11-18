"""Verify guarded account saves persist their complete transactional outcome."""

import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
import pytest

from app.admin.repository import AdminRepository
from app.admin.schemas import UserInput
from app.admin.service import save_user
from app.core.security import verify_password


@pytest.mark.asyncio
async def test_guarded_user_save_persists_assignments_and_audit():
    """A guarded create commits account, both references, and its audit event."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_REPLICA_URI to a test replica set')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'admin_guard_outcome_' + uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command('hello')
        assert hello.get('setName'), 'TEST_MONGODB_REPLICA_URI must target a replica set'
        await db.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['administer']},
            {'name': 'Engineer', 'permissions': ['view']},
        ])
        actor = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                 'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
        await db.users.insert_one(actor.copy())
        first, second = ObjectId(), ObjectId()
        await db.portfolios.insert_many([
            {'_id': first, 'name': 'First'}, {'_id': second, 'name': 'Second'},
        ])
        body = UserInput(name='New Engineer', email='new@example.com', role='Engineer',
                         portfolioAccess=[str(second), str(first)], password='Strong-Password123!')

        result = await save_user(AdminRepository(db), actor, body)
        account = await db.users.find_one({'_id': ObjectId(result['id'])})
        assert result['email'] == account['email'] == 'new@example.com'
        assert result['name'] == account['name'] == 'New Engineer'
        assert account['role'] == 'Engineer' and account['status'] == 'Active'
        assert account['portfolioAccess'] == result['portfolioAccess'] == [str(second), str(first)]
        assert verify_password('Strong-Password123!', account['passwordHash'])
        assert account['tokenVersion'] == 0 and account['mustChangePassword'] is True
        for key in (first, second):
            assert (await db.portfolios.find_one({'_id': key}))['_referenceVersion'] == 1
        events = await db.audit_logs.find({'entityId': result['id']}).to_list(None)
        assert len(events) == 1
        assert events[0]['action'] == 'create' and events[0]['entityType'] == 'user'
        assert events[0]['userId'] == actor['_id'] and events[0]['details'] == 'User created'
        assert 'Strong-Password123!' not in str(events)
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
async def test_guarded_user_save_denies_stale_session_without_writes():
    """A stale administrator session cannot update an account or write an audit."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_REPLICA_URI to a test replica set')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'admin_guard_denied_' + uuid4().hex
    db = client[name]
    try:
        assert (await client.admin.command('hello')).get('setName')
        await db.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['administer']},
            {'name': 'Engineer', 'permissions': ['view']},
        ])
        actor = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                 'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
        target = {'_id': ObjectId(), 'name': 'Before', 'email': 'engineer@example.com',
                  'role': 'Engineer', 'status': 'Active', 'portfolioAccess': [], 'tokenVersion': 0}
        await db.users.insert_many([{**actor, 'tokenVersion': 1}, target.copy()])
        portfolio = (await db.portfolios.insert_one({'name': 'Portfolio'})).inserted_id
        await db.audit_logs.insert_one({'action': 'existing', 'entityType': 'user'})
        users_before = await db.users.find().sort('_id').to_list(None)
        roles_before = await db.roles.find().sort('_id').to_list(None)
        portfolios_before = await db.portfolios.find().sort('_id').to_list(None)
        audit_before = await db.audit_logs.find().sort('_id').to_list(None)
        body = UserInput(name='After', email=target['email'], role='Engineer',
                         portfolioAccess=[str(portfolio)])

        with pytest.raises(HTTPException) as error:
            await save_user(AdminRepository(db), actor, body, str(target['_id']))
        assert error.value.status_code == 403
        assert error.value.detail == 'Not permitted'
        assert await db.users.find().sort('_id').to_list(None) == users_before
        assert await db.roles.find().sort('_id').to_list(None) == roles_before
        assert await db.portfolios.find().sort('_id').to_list(None) == portfolios_before
        assert await db.audit_logs.find().sort('_id').to_list(None) == audit_before
    finally:
        await client.drop_database(name)
        await client.close()
