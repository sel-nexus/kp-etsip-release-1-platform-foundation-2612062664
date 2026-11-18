"""Exercise administration service branches against a real MongoDB database."""

import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
import pytest
import pytest_asyncio

from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.admin.service import authorize, deactivate, get_doc, ident, present_role, present_user, save_role, save_user
from app.core.security import verify_password


@pytest_asyncio.fixture
async def mongo_repo():
    """Create an isolated MongoDB database with representative roles and users."""
    client = AsyncMongoClient(os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true'), serverSelectionTimeoutMS=5000)
    name = 'ketsip_admin_service_' + uuid4().hex
    db = client[name]
    try:
        await client.admin.command('ping')
        await db.users.create_index('email', unique=True)
        await db.roles.create_index('name', unique=True)
        await db.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['administer', 'view']},
            {'name': 'Engineer', 'permissions': ['view']},
            {'name': 'Read Only', 'permissions': ['view']},
        ])
        actor = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                 'role': 'Administrator', 'status': 'Active', 'tokenVersion': 2}
        await db.users.insert_one(actor)
        portfolio = (await db.portfolios.insert_one({'name': 'Portfolio'})).inserted_id
        yield AdminRepository(db), actor, portfolio
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
async def test_service_user_create_update_deactivate_and_persisted_state(mongo_repo):
    repo, actor, portfolio = mongo_repo
    body = UserInput(name='  Engineer  ', email='USER@example.com', role='Engineer',
                     portfolioAccess=[str(portfolio)], password='Strong-Password123!')
    created = await save_user(repo, actor, body)
    assert created['name'] == 'Engineer' and created['email'] == 'user@example.com'
    assert 'passwordHash' not in created and created['mustChangePassword'] is True
    key = ObjectId(created['id'])
    stored = await repo.db.users.find_one({'_id': key})
    assert verify_password('Strong-Password123!', stored['passwordHash'])
    assert stored['portfolioAccess'] == [str(portfolio)] and stored['tokenVersion'] == 0
    assert (await get_doc(repo, 'users', created['id']))['_id'] == key
    assert ident(created['id']) == key
    assert present_user(stored)['authSource'] == 'local'

    changed = body.model_copy(update={'name': 'Updated', 'role': 'Read Only', 'password': 'New-Password123!'})
    result = await save_user(repo, actor, changed, created['id'])
    assert result['role'] == 'Read Only' and result['name'] == 'Updated'
    stored = await repo.db.users.find_one({'_id': key})
    assert stored['role'] == 'Read Only' and stored['name'] == 'Updated'
    assert stored['tokenVersion'] == 1 and verify_password('New-Password123!', stored['passwordHash'])
    assert await repo.db.audit_logs.count_documents({'entityId': created['id'], 'action': 'roleChange'}) == 1

    await deactivate(repo, actor, created['id'])
    stored = await repo.db.users.find_one({'_id': key})
    assert stored['status'] == 'Inactive' and stored['tokenVersion'] == 2
    assert await repo.db.audit_logs.count_documents({'entityId': created['id'], 'action': 'delete'}) == 1


@pytest.mark.asyncio
async def test_service_user_failures_preserve_database(mongo_repo):
    repo, actor, portfolio = mongo_repo
    body = UserInput(name='Person', email='person@example.com', role='Engineer',
                     portfolioAccess=[str(portfolio)], password='Strong-Password123!')
    created = await save_user(repo, actor, body)
    key = ObjectId(created['id'])
    before = await repo.db.users.find_one({'_id': key})
    cases = [
        (body, None, 409, 'Email already exists'),
        (body.model_copy(update={'email': 'missing@example.com', 'password': None}), None, 422, 'Password is required'),
        (body.model_copy(update={'portfolioAccess': [str(ObjectId())]}), created['id'], 422, 'Portfolio assignments'),
        (body.model_copy(update={'portfolioAccess': [str(portfolio), str(portfolio)]}), created['id'], 422, 'Portfolio assignments'),
        (body.model_copy(update={'role': 'Auditor'}), created['id'], 422, 'Role does not exist'),
        (body, str(ObjectId()), 404, 'Resource not found'),
        (body, 'bad-id', 404, 'Resource not found'),
    ]
    for payload, value, code, message in cases:
        with pytest.raises(HTTPException) as exc:
            await save_user(repo, actor, payload, value)
        assert exc.value.status_code == code and message in exc.value.detail
    assert await repo.db.users.find_one({'_id': key}) == before
    for value in ('bad-id', str(ObjectId())):
        with pytest.raises(HTTPException) as exc:
            await deactivate(repo, actor, value)
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await get_doc(repo, 'users', str(ObjectId()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('case,code,detail', [
    pytest.param('malformed-portfolio', 422, 'Portfolio assignments', id='malformed-portfolio'),
    pytest.param('missing-portfolio', 422, 'Portfolio assignments', id='missing-portfolio'),
    pytest.param('duplicate-portfolio', 422, 'Portfolio assignments', id='duplicate-portfolio'),
    pytest.param('missing-role', 422, 'Role does not exist', id='missing-role'),
    pytest.param('missing-password', 422, 'Password is required', id='missing-password'),
    pytest.param('invalid-user-id', 404, 'Resource not found', id='invalid-user-id'),
    pytest.param('missing-user', 404, 'Resource not found', id='missing-user'),
    pytest.param('duplicate-email', 409, 'Email already exists', id='duplicate-email'),
])
async def test_save_user_rejects_each_branch_without_mutation(mongo_repo, case, code, detail):
    """Direct service failures preserve all account, guard, and audit documents."""
    repo, actor, portfolio = mongo_repo
    body = UserInput(name='Person', email='person@example.com', role='Engineer',
                     portfolioAccess=[str(portfolio)], password='Strong-Password123!')
    value = None
    if case == 'malformed-portfolio':
        body = body.model_copy(update={'portfolioAccess': ['bad-id']})
    elif case == 'missing-portfolio':
        body = body.model_copy(update={'portfolioAccess': [str(ObjectId())]})
    elif case == 'duplicate-portfolio':
        body = body.model_copy(update={'portfolioAccess': [str(portfolio), str(portfolio).upper()]})
    elif case == 'missing-role':
        body = body.model_copy(update={'role': 'Auditor'})
    elif case == 'missing-password':
        body = body.model_copy(update={'password': None})
    elif case == 'invalid-user-id':
        value = 'bad-id'
    elif case == 'missing-user':
        value = str(ObjectId())
    elif case == 'duplicate-email':
        body = body.model_copy(update={'email': actor['email']})
    collections = ('users', 'roles', 'portfolios', 'audit_logs')
    before = {name: await repo.db[name].find().sort('_id').to_list(None) for name in collections}
    with pytest.raises(HTTPException) as exc:
        await save_user(repo, actor, body, value)
    assert exc.value.status_code == code
    assert detail in exc.value.detail
    for name in collections:
        assert await repo.db[name].find().sort('_id').to_list(None) == before[name]


@pytest.mark.asyncio
@pytest.mark.parametrize('case,code,detail', [
    pytest.param('duplicate', 409, 'Role already exists', id='duplicate'),
    pytest.param('rename', 409, 'Role names cannot be changed', id='rename'),
    pytest.param('remove-administer', 409, 'Administrator must retain', id='remove-administer'),
    pytest.param('invalid-id', 404, 'Resource not found', id='invalid-id'),
    pytest.param('missing-id', 404, 'Resource not found', id='missing-id'),
])
async def test_save_role_rejects_each_branch_without_mutation(mongo_repo, case, code, detail):
    """Role conflicts and invalid IDs leave roles, guard writes and audit unchanged."""
    repo, actor, _ = mongo_repo
    engineer = await repo.db.roles.find_one({'name': 'Engineer'})
    body = RoleInput(name='Engineer', permissions=['view'])
    value = None
    if case == 'rename':
        body = RoleInput(name='Auditor', permissions=['view'])
        value = str(engineer['_id'])
    elif case == 'remove-administer':
        body = RoleInput(name='Administrator', permissions=['view'])
        value = str((await repo.db.roles.find_one({'name': 'Administrator'}))['_id'])
    elif case == 'invalid-id':
        value = 'bad-id'
    elif case == 'missing-id':
        value = str(ObjectId())
    collections = ('users', 'roles', 'portfolios', 'audit_logs')
    before = {name: await repo.db[name].find().sort('_id').to_list(None) for name in collections}
    with pytest.raises(HTTPException) as exc:
        await save_role(repo, actor, body, value)
    assert exc.value.status_code == code
    assert detail in exc.value.detail
    for name in collections:
        assert await repo.db[name].find().sort('_id').to_list(None) == before[name]


@pytest.mark.asyncio
async def test_service_authorization_and_last_active_admin_protection(mongo_repo):
    repo, actor, _ = mongo_repo
    await authorize(repo, actor, 'administer', {'Administrator'})
    for user, permission, roles in [
        ({**actor, 'role': 'Engineer'}, 'view', {'Administrator'}),
        (actor, 'delete', {'Administrator'}),
        ({**actor, 'role': 'Auditor'}, 'view', {'Auditor'}),
    ]:
        with pytest.raises(HTTPException) as exc:
            await authorize(repo, user, permission, roles)
        assert exc.value.status_code == 403
    unauthorized = {**actor, 'role': 'Engineer'}
    for operation in (
        save_user(repo, unauthorized, UserInput(name='Denied', email='denied@example.com', role='Engineer', password='Strong-Password123!')),
        save_role(repo, unauthorized, RoleInput(name='Auditor', permissions=['view'])),
        deactivate(repo, unauthorized, str(actor['_id'])),
    ):
        with pytest.raises(HTTPException) as exc:
            await operation
        assert exc.value.status_code == 403
    assert await repo.db.users.count_documents({'email': 'denied@example.com'}) == 0
    assert await repo.db.roles.count_documents({'name': 'Auditor'}) == 0
    body = UserInput(name=actor['name'], email=actor['email'], role='Engineer')
    for update in (body, body.model_copy(update={'role': 'Administrator', 'status': 'Inactive'})):
        with pytest.raises(HTTPException) as exc:
            await save_user(repo, actor, update, str(actor['_id']))
        assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await deactivate(repo, actor, str(actor['_id']))
    assert exc.value.status_code == 409
    stored = await repo.db.users.find_one({'_id': actor['_id']})
    assert stored['role'] == 'Administrator' and stored['status'] == 'Active'

    second = await save_user(repo, actor, UserInput(name='Second admin', email='second@example.com',
                             role='Administrator', password='Strong-Password123!'))
    await deactivate(repo, actor, second['id'])
    assert (await repo.db.users.find_one({'_id': ObjectId(second['id'])}))['status'] == 'Inactive'


@pytest.mark.asyncio
async def test_service_update_inactive_invalidates_session_without_password_reset(mongo_repo):
    repo, actor, _ = mongo_repo
    body = UserInput(name='Engineer', email='engineer@example.com', role='Engineer',
                     password='Strong-Password123!')
    created = await save_user(repo, actor, body)
    key = ObjectId(created['id'])
    before = await repo.db.users.find_one({'_id': key})
    updated = body.model_copy(update={'status': 'Inactive', 'password': None})
    response = await save_user(repo, actor, updated, created['id'])
    assert response['status'] == 'Inactive'
    stored = await repo.db.users.find_one({'_id': key})
    assert stored['status'] == 'Inactive' and stored['tokenVersion'] == before['tokenVersion'] + 1
    assert stored['passwordHash'] == before['passwordHash']


@pytest.mark.asyncio
async def test_service_role_create_update_and_conflicts(mongo_repo):
    repo, actor, _ = mongo_repo
    body = RoleInput(name='Auditor', description='Audit', permissions=['view', 'export'])
    created = await save_role(repo, actor, body)
    key = ObjectId(created['id'])
    stored = await repo.db.roles.find_one({'_id': key})
    assert present_role(stored) == created
    assert stored['permissions'] == ['view', 'export'] and stored['description'] == 'Audit'
    updated = body.model_copy(update={'description': 'Restricted', 'permissions': ['view']})
    result = await save_role(repo, actor, updated, created['id'])
    assert result['description'] == 'Restricted' and result['permissions'] == ['view']
    stored = await repo.db.roles.find_one({'_id': key})
    assert stored['description'] == 'Restricted' and stored['permissions'] == ['view']
    assert await repo.db.audit_logs.count_documents({'entityId': created['id'], 'action': 'permissionChange'}) == 2
    await save_role(repo, actor, updated, created['id'])
    assert await repo.db.audit_logs.count_documents({'entityId': created['id'], 'action': 'permissionChange'}) == 2
    cases = [
        (body, None, 409, 'Role already exists'),
        (RoleInput(name='Engineer', permissions=['view']), created['id'], 409, 'Role names cannot be changed'),
        (RoleInput(name='Administrator', permissions=['view']), None, 409, 'Administrator must retain'),
        (body, 'bad-id', 404, 'Resource not found'),
        (body, str(ObjectId()), 404, 'Resource not found'),
    ]
    for payload, value, code, message in cases:
        with pytest.raises(HTTPException) as exc:
            await save_role(repo, actor, payload, value)
        assert exc.value.status_code == code and message in exc.value.detail
    assert (await repo.db.roles.find_one({'_id': key}))['permissions'] == ['view']
