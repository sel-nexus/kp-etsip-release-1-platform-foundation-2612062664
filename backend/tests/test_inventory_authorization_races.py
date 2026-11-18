"""Verify inventory authorization against concurrent admin changes on a replica set."""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure

from app.admin import service as admin
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.applications import service as applications
from app.applications.repository import ApplicationRepository
from app.applications.schemas import ApplicationInput
from app.portfolios import service as portfolios
from app.portfolios.repository import PortfolioRepository
from app.portfolios.schemas import PortfolioInput


@pytest_asyncio.fixture
async def db():
    """Use a unique disposable replica-set database."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_REPLICA_URI to a test replica set')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'inventory_auth_races_' + uuid4().hex
    db = client[name]
    try:
        assert (await client.admin.command('hello')).get('setName')
        await db.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['administer', 'view', 'create', 'edit', 'delete']},
            {'name': 'Portfolio Leader', 'permissions': ['view', 'create', 'edit']},
            {'name': 'Read Only', 'permissions': ['view']},
        ])
        yield db
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['application', 'portfolio'])
@pytest.mark.parametrize('change', ['permission', 'role', 'assignment', 'deactivate', 'token'])
async def test_admin_revocation_forces_inventory_retry_and_reauthorization(db, monkeypatch, kind, change):
    """A committed concurrent admin change prevents the stale inventory write."""
    pid = (await db.portfolios.insert_one({'name': 'Before', 'owner': 'Before'})).inserted_id
    app_id = (await db.applications.insert_one({'name': 'Before app', 'portfolioId': str(pid)})).inserted_id
    actor = {'_id': ObjectId(), 'name': 'Leader', 'email': 'leader@example.com',
             'role': 'Portfolio Leader', 'status': 'Active', 'tokenVersion': 0,
             'portfolioAccess': [str(pid)]}
    administrator = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                     'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
    await db.users.insert_many([actor.copy(), administrator.copy()])
    role = await db.roles.find_one({'name': 'Portfolio Leader'})
    admin_repo = AdminRepository(db)
    inventory_repo = ApplicationRepository(db) if kind == 'application' else PortfolioRepository(db)
    module = applications if kind == 'application' else portfolios
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    original_audit = admin_repo.audit
    original_transact = module.transact
    attempts = 0

    async def pause_admin(*args):
        await original_audit(*args)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    async def count_transactions(database, callback):
        async def count(session):
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                await asyncio.wait_for(committed.wait(), 15)
            try:
                return await callback(session)
            except OperationFailure as exc:
                if exc.has_error_label('TransientTransactionError'):
                    conflict.set()
                raise
        return await original_transact(database, count)

    monkeypatch.setattr(admin_repo, 'audit', pause_admin)
    monkeypatch.setattr(module, 'transact', count_transactions)
    if change == 'permission':
        change_call = admin.save_role(admin_repo, administrator,
            RoleInput(name='Portfolio Leader', permissions=['view']), str(role['_id']))
    elif change == 'deactivate':
        change_call = admin.deactivate(admin_repo, administrator, str(actor['_id']))
    else:
        body = UserInput(name='Leader', email=actor['email'],
                         role='Read Only' if change == 'role' else actor['role'],
                         portfolioAccess=[] if change == 'assignment' else [str(pid)],
                         password='Strong-Password123!' if change == 'token' else None)
        change_call = admin.save_user(admin_repo, administrator, body, str(actor['_id']))
    writer = asyncio.create_task(change_call)
    contender = None
    try:
        await asyncio.wait_for(reached.wait(), 15)
        if kind == 'application':
            call = applications.mutate(inventory_repo, actor, 'edit', str(app_id),
                ApplicationInput(name='After app', portfolioId=str(pid)))
        else:
            call = portfolios.mutate(inventory_repo, actor, 'edit', str(pid),
                PortfolioInput(name='After', owner='After'))
        contender = asyncio.create_task(call)
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(writer, 15)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(contender, 15)
        assert error.value.status_code == 403
        assert attempts >= 2
        assert (await db.applications.find_one({'_id': app_id}))['name'] == 'Before app'
        assert (await db.portfolios.find_one({'_id': pid}))['name'] == 'Before'
        assert await db.audit_logs.count_documents({'entityType': kind}) == 0
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, contender) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['application', 'portfolio'])
async def test_stale_scope_is_not_used_and_live_scope_grants_access(db, kind):
    """Re-read assignments rather than trusting the authenticated snapshot."""
    old = (await db.portfolios.insert_one({'name': 'Old'})).inserted_id
    new = (await db.portfolios.insert_one({'name': 'New'})).inserted_id
    actor = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader',
             'status': 'Active', 'tokenVersion': 0, 'portfolioAccess': [str(old)]}
    await db.users.insert_one({**actor, 'portfolioAccess': [str(new)]})
    if kind == 'application':
        repo = ApplicationRepository(db)
        with pytest.raises(HTTPException) as error:
            await applications.mutate(repo, actor, 'create', body=ApplicationInput(name='Forbidden', portfolioId=str(old)))
        assert error.value.status_code == 403
        result = await applications.mutate(repo, actor, 'create', body=ApplicationInput(name='Allowed', portfolioId=str(new)))
        assert result['portfolioId'] == str(new)
    else:
        repo = PortfolioRepository(db)
        with pytest.raises(HTTPException) as error:
            await portfolios.mutate(repo, actor, 'edit', str(old), PortfolioInput(name='Forbidden'))
        assert error.value.status_code == 403
        result = await portfolios.mutate(repo, actor, 'edit', str(new), PortfolioInput(name='Allowed'))
        assert result['name'] == 'Allowed'
    assert await db.audit_logs.count_documents({'entityType': kind}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['application', 'portfolio'])
@pytest.mark.parametrize('failure', ['missing', 'inactive', 'role', 'token', 'password', 'missing-role'])
async def test_stale_or_restricted_actor_fails_closed(db, kind, failure):
    """Reject invalid sessions and absent grants without inventory or audit writes."""
    pid = (await db.portfolios.insert_one({'name': 'Original'})).inserted_id
    actor = {'_id': ObjectId(), 'name': 'Admin', 'role': 'Administrator',
             'status': 'Active', 'tokenVersion': 0}
    if failure != 'missing':
        await db.users.insert_one(actor.copy())
    if failure == 'inactive':
        await db.users.update_one({'_id': actor['_id']}, {'$set': {'status': 'Inactive'}})
    elif failure == 'role':
        await db.users.update_one({'_id': actor['_id']}, {'$set': {'role': 'Read Only'}})
    elif failure == 'token':
        await db.users.update_one({'_id': actor['_id']}, {'$inc': {'tokenVersion': 1}})
    elif failure == 'password':
        await db.users.update_one({'_id': actor['_id']}, {'$set': {'mustChangePassword': True}})
    elif failure == 'missing-role':
        await db.roles.delete_one({'name': 'Administrator'})
    if kind == 'application':
        call = applications.mutate(ApplicationRepository(db), actor, 'create',
                                   body=ApplicationInput(name='Forbidden', portfolioId=str(pid)))
    else:
        call = portfolios.mutate(PortfolioRepository(db), actor, 'create', body=PortfolioInput(name='Forbidden'))
    with pytest.raises(HTTPException) as error:
        await call
    assert error.value.status_code == 403
    assert await db.applications.count_documents({}) == 0
    assert await db.portfolios.count_documents({}) == 1
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['application', 'portfolio'])
async def test_admin_create_edit_delete_commit_and_audit(db, kind):
    """A current unrestricted administrator can commit each mutation."""
    actor = {'_id': ObjectId(), 'name': 'Admin', 'role': 'Administrator',
             'status': 'Active', 'tokenVersion': 0}
    await db.users.insert_one(actor.copy())
    if kind == 'application':
        pid = (await db.portfolios.insert_one({'name': 'Portfolio'})).inserted_id
        repo = ApplicationRepository(db)
        created = await applications.mutate(repo, actor, 'create', body=ApplicationInput(name='Before', portfolioId=str(pid)))
        await applications.mutate(repo, actor, 'edit', created['id'], ApplicationInput(name='After', portfolioId=str(pid)))
        assert (await db.applications.find_one({'_id': ObjectId(created['id'])}))['name'] == 'After'
        await applications.mutate(repo, actor, 'delete', created['id'])
        assert await db.applications.count_documents({}) == 0
    else:
        repo = PortfolioRepository(db)
        created = await portfolios.mutate(repo, actor, 'create', body=PortfolioInput(name='Before'))
        await portfolios.mutate(repo, actor, 'edit', created['id'], PortfolioInput(name='After'))
        assert (await db.portfolios.find_one({'_id': ObjectId(created['id'])}))['name'] == 'After'
        await portfolios.mutate(repo, actor, 'delete', created['id'])
        assert await db.portfolios.count_documents({}) == 0
    assert await db.audit_logs.count_documents({'entityType': kind}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['application', 'portfolio'])
async def test_inventory_commits_before_competing_permission_revocation(db, monkeypatch, kind):
    """A revocation waiting on an authorized mutation runs after its commit."""
    pid = (await db.portfolios.insert_one({'name': 'Before'})).inserted_id
    app_id = (await db.applications.insert_one({'name': 'Before app', 'portfolioId': str(pid)})).inserted_id
    actor = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader',
             'status': 'Active', 'tokenVersion': 0, 'portfolioAccess': [str(pid)]}
    administrator = {'_id': ObjectId(), 'name': 'Admin', 'role': 'Administrator',
                     'status': 'Active', 'tokenVersion': 0}
    await db.users.insert_many([actor.copy(), administrator.copy()])
    role = await db.roles.find_one({'name': 'Portfolio Leader'})
    repo = ApplicationRepository(db) if kind == 'application' else PortfolioRepository(db)
    admin_repo = AdminRepository(db)
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    original_audit = repo.audit
    original_update = admin_repo.update
    original_transact = admin.transact
    attempts = 0
    transaction_attempts = 0

    async def pause_inventory(*args):
        await original_audit(*args)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    async def count_update(*args):
        nonlocal attempts
        attempts += 1
        return await original_update(*args)

    async def observe_transactions(database, callback):
        async def observe(session):
            nonlocal transaction_attempts
            transaction_attempts += 1
            if transaction_attempts > 1:
                await asyncio.wait_for(committed.wait(), 15)
            try:
                return await callback(session)
            except OperationFailure as exc:
                if exc.has_error_label('TransientTransactionError'):
                    conflict.set()
                raise
        return await original_transact(database, observe)

    monkeypatch.setattr(admin, 'transact', observe_transactions)
    monkeypatch.setattr(repo, 'audit', pause_inventory)
    monkeypatch.setattr(admin_repo, 'update', count_update)
    if kind == 'application':
        call = applications.mutate(repo, actor, 'edit', str(app_id),
                                   ApplicationInput(name='After app', portfolioId=str(pid)))
    else:
        call = portfolios.mutate(repo, actor, 'edit', str(pid), PortfolioInput(name='After'))
    writer = asyncio.create_task(call)
    contender = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        contender = asyncio.create_task(admin.save_role(admin_repo, administrator,
            RoleInput(name='Portfolio Leader', permissions=['view']), str(role['_id'])))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(writer, 15)
        committed.set()
        await asyncio.wait_for(contender, 15)
        assert attempts >= 2
        assert transaction_attempts >= 2
        assert (await db.roles.find_one({'_id': role['_id']}))['permissions'] == ['view']
        assert (await db.applications.find_one({'_id': app_id}))['name'] == ('After app' if kind == 'application' else 'Before app')
        assert (await db.portfolios.find_one({'_id': pid}))['name'] == ('After' if kind == 'portfolio' else 'Before')
        assert await db.audit_logs.count_documents({'entityType': kind, 'action': 'edit'}) == 1
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, contender) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
