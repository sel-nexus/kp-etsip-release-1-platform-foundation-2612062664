"""Exercise administration atomicity against a disposable MongoDB replica set."""

import asyncio
import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
import pytest
import pytest_asyncio

from app.admin import service as admin
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.audit.repository import AuditRepository
from app.auth import service as auth
from app.auth.repository import UserRepository
from app.core.security import hash_password
from app.portfolios import service as portfolios
from app.portfolios.repository import PortfolioRepository


@pytest_asyncio.fixture
async def db():
    """Create and destroy only a uniquely named test database."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_REPLICA_URI to a test replica set')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'admin_atomicity_' + uuid4().hex
    database = client[name]
    try:
        hello = await client.admin.command('hello')
        assert hello.get('setName'), 'TEST_MONGODB_REPLICA_URI must target a replica set'
        await database.roles.create_index('name', unique=True)
        await database.users.create_index('email', unique=True)
        await database.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['administer', 'delete']},
            {'name': 'Engineer', 'permissions': ['view']},
        ])
        yield database
    finally:
        await client.drop_database(name)
        await client.close()


async def make_admin(db, email='admin@example.com'):
    """Insert an active administrator with a stable authentication version."""
    actor = {'_id': ObjectId(), 'name': email, 'email': email, 'role': 'Administrator',
             'status': 'Active', 'tokenVersion': 0, 'mustChangePassword': False, 'portfolioAccess': []}
    await db.users.insert_one(actor.copy())
    return actor


def user_body(email='user@example.com', portfolios=()):
    """Build a valid engineer account request."""
    return UserInput(name='User', email=email, role='Engineer',
                     portfolioAccess=list(portfolios), password='Strong-Password123!')


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['create', 'update', 'user_role_update', 'deactivate', 'role_create', 'role_update'])
async def test_audit_failure_rolls_back_all_writes(db, monkeypatch, action):
    """Audit insertion failure rolls back the mutation and all guard writes."""
    actor = await make_admin(db)
    user = {'name': 'Before', 'email': 'user@example.com', 'role': 'Engineer',
            'status': 'Active', 'portfolioAccess': [], 'tokenVersion': 0}
    user_id = (await db.users.insert_one(user)).inserted_id
    role_id = (await db.roles.find_one({'name': 'Engineer'}))['_id']
    portfolio_id = (await db.portfolios.insert_one({'name': 'Portfolio'})).inserted_id
    before = {name: await db[name].find().sort('_id').to_list(None)
              for name in ('users', 'roles', 'portfolios', 'audit_logs')}
    repo = AdminRepository(db)
    original = repo.audit
    count = 0

    async def fail_after_audit(*args):
        nonlocal count
        await original(*args)
        count += 1
        # Exercise rollback of a second role/permission audit where applicable.
        if action not in ('user_role_update', 'role_create', 'role_update') or count == 2:
            raise RuntimeError('injected audit failure')

    monkeypatch.setattr(repo, 'audit', fail_after_audit)
    if action == 'create':
        call = admin.save_user(repo, actor, user_body('new@example.com', [str(portfolio_id)]))
    elif action == 'update':
        call = admin.save_user(repo, actor, user_body(portfolios=[str(portfolio_id)]), str(user_id))
    elif action == 'user_role_update':
        body = user_body(portfolios=[str(portfolio_id)]).model_copy(update={'role': 'Administrator'})
        call = admin.save_user(repo, actor, body, str(user_id))
    elif action == 'deactivate':
        call = admin.deactivate(repo, actor, str(user_id))
    elif action == 'role_create':
        call = admin.save_role(repo, actor, RoleInput(name='Auditor', permissions=['view']))
    else:
        call = admin.save_role(repo, actor, RoleInput(name='Engineer', permissions=['edit']), str(role_id))
    with pytest.raises(RuntimeError, match='injected audit failure'):
        await call
    for name, documents in before.items():
        assert await db[name].find().sort('_id').to_list(None) == documents


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['deactivate', 'demote'])
async def test_last_administrators_are_serialized_and_rechecked(db, monkeypatch, action):
    """Concurrent removals cannot both pass the last-active-administrator check."""
    first = await make_admin(db, 'one@example.com')
    second = await make_admin(db, 'two@example.com')
    first_repo, second_repo = AdminRepository(db), AdminRepository(db)
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    original_audit, original_guard = first_repo.audit, second_repo.guard_admin
    attempts = 0

    async def pause_audit(*args):
        await original_audit(*args)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    async def count_guard(*args):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            await asyncio.wait_for(committed.wait(), 15)
        try:
            return await original_guard(*args)
        except OperationFailure as exc:
            if exc.has_error_label('TransientTransactionError'):
                conflict.set()
            raise

    monkeypatch.setattr(first_repo, 'audit', pause_audit)
    monkeypatch.setattr(second_repo, 'guard_admin', count_guard)
    def remove(repo, actor):
        if action == 'deactivate':
            return admin.deactivate(repo, actor, str(actor['_id']))
        body = UserInput(name=actor['name'], email=actor['email'], role='Engineer')
        return admin.save_user(repo, actor, body, str(actor['_id']))
    writer = asyncio.create_task(remove(first_repo, first))
    contender = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        contender = asyncio.create_task(remove(second_repo, second))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(writer, 15)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(contender, 15)
        assert error.value.status_code == 409
        assert attempts >= 2
        assert await db.users.count_documents({'role': 'Administrator', 'status': 'Active'}) == 1
        assert await db.audit_logs.count_documents({'entityType': 'user', 'action': 'delete' if action == 'deactivate' else 'edit'}) == 1
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, contender) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['create', 'update'])
@pytest.mark.parametrize('winner', ['assignment', 'deletion'])
async def test_assignment_and_deletion_cannot_orphan_reference(db, monkeypatch, action, winner):
    """Both orderings retry against the other transaction's committed result."""
    actor = await make_admin(db)
    target = (await db.portfolios.insert_one({'name': 'Target'})).inserted_id
    existing = (await db.users.insert_one({'name': 'Before', 'email': 'user@example.com',
                  'role': 'Engineer', 'status': 'Active', 'portfolioAccess': []})).inserted_id
    admin_repo, portfolio_repo = AdminRepository(db), PortfolioRepository(db)
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    first_repo = admin_repo if winner == 'assignment' else portfolio_repo
    second_module = portfolios if winner == 'assignment' else admin
    original_audit, original_transact = first_repo.audit, second_module.transact
    attempts = 0

    async def pause_audit(*args):
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

    monkeypatch.setattr(first_repo, 'audit', pause_audit)
    monkeypatch.setattr(second_module, 'transact', count_transactions)
    def assign():
        email = 'new@example.com' if action == 'create' else 'user@example.com'
        return admin.save_user(admin_repo, actor, user_body(email, [str(target)]),
                               None if action == 'create' else str(existing))
    def delete():
        return portfolios.mutate(portfolio_repo, actor, 'delete', str(target))
    writer = asyncio.create_task(assign() if winner == 'assignment' else delete())
    contender = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        contender = asyncio.create_task(delete() if winner == 'assignment' else assign())
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(writer, 15)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(contender, 15)
        assert error.value.status_code == (409 if winner == 'assignment' else 422)
        assert attempts >= 2
        assert bool(await db.portfolios.find_one({'_id': target})) == (winner == 'assignment')
        assert await db.users.count_documents({'portfolioAccess': str(target)}) == (1 if winner == 'assignment' else 0)
        assert await db.audit_logs.count_documents({'entityType': 'user'}) == (1 if winner == 'assignment' else 0)
        assert await db.audit_logs.count_documents({'entityType': 'portfolio', 'action': 'delete'}) == (0 if winner == 'assignment' else 1)
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, contender) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
async def test_live_actor_and_permission_are_rechecked(db):
    """Stale request identities and revoked permissions cannot authorize a mutation."""
    actor = await make_admin(db)
    repo = AdminRepository(db)
    await db.users.update_one({'_id': actor['_id']}, {'$set': {'role': 'Engineer'}})
    with pytest.raises(HTTPException) as error:
        await admin.save_role(repo, actor, RoleInput(name='Auditor', permissions=['view']))
    assert error.value.status_code == 403
    await db.users.update_one({'_id': actor['_id']}, {'$set': {'role': 'Administrator', 'tokenVersion': 1}})
    with pytest.raises(HTTPException) as error:
        await admin.deactivate(repo, actor, str(actor['_id']))
    assert error.value.status_code == 403
    await db.users.update_one({'_id': actor['_id']}, {'$set': {'tokenVersion': 0}})
    await db.roles.update_one({'name': 'Administrator'}, {'$set': {'permissions': ['delete']}})
    with pytest.raises(HTTPException) as error:
        await admin.save_user(repo, actor, user_body())
    assert error.value.status_code == 403
    assert await db.roles.count_documents({'name': 'Auditor'}) == 0
    assert await db.users.count_documents({'email': 'user@example.com'}) == 0
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('revocation', ['logout', 'password_change'])
async def test_legacy_session_revoked_during_admin_mutation(db, monkeypatch, revocation):
    """Reject a legacy actor when logout or password change wins the race."""
    actor = await make_admin(db)
    actor.pop('tokenVersion')
    actor['passwordHash'] = hash_password('Original-Password123!')
    await db.users.update_one({'_id': actor['_id']}, {
        '$unset': {'tokenVersion': ''}, '$set': {'passwordHash': actor['passwordHash']}
    })
    repo = AdminRepository(db)
    reached, release = asyncio.Event(), asyncio.Event()
    original_guard = repo.guard_admin

    async def pause_guard(*args):
        reached.set()
        await asyncio.wait_for(release.wait(), 10)
        return await original_guard(*args)

    monkeypatch.setattr(repo, 'guard_admin', pause_guard)
    writer = asyncio.create_task(admin.save_role(repo, actor, RoleInput(name='Auditor', permissions=['view'])))
    try:
        await asyncio.wait_for(reached.wait(), 10)
        users, audit = UserRepository(db), AuditRepository(db)
        if revocation == 'logout':
            await auth.logout(users, audit, actor)
        else:
            await auth.change_password(users, audit, actor, 'Original-Password123!',
                                       'Replacement-Password123!', 'test-secret')
        release.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(writer, 15)
        assert error.value.status_code == 403
        assert (await db.users.find_one({'_id': actor['_id']}))['tokenVersion'] == 1
        assert await db.roles.count_documents({'name': 'Auditor'}) == 0
        assert await db.audit_logs.count_documents({'entityType': 'role'}) == 0
        assert await db.audit_logs.count_documents({}) == 1
    finally:
        release.set()
        if not writer.done():
            writer.cancel()
        await asyncio.wait_for(asyncio.gather(writer, return_exceptions=True), 10)


@pytest.mark.asyncio
async def test_unchanged_legacy_session_can_mutate(db):
    """Continue to authorize an active legacy actor whose version is absent."""
    actor = await make_admin(db)
    actor.pop('tokenVersion')
    await db.users.update_one({'_id': actor['_id']}, {'$unset': {'tokenVersion': ''}})
    result = await admin.save_role(AdminRepository(db), actor, RoleInput(name='Auditor', permissions=['view']))
    assert result['name'] == 'Auditor'
    assert await db.roles.count_documents({'name': 'Auditor'}) == 1
    assert await db.audit_logs.count_documents({'entityType': 'role'}) == 2


@pytest.mark.asyncio
async def test_role_permission_change_is_audited(db):
    """A successful permission update commits its role and audit records together."""
    actor = await make_admin(db)
    role = await db.roles.find_one({'name': 'Engineer'})
    result = await admin.save_role(AdminRepository(db), actor,
                                   RoleInput(name='Engineer', permissions=['view', 'edit']), str(role['_id']))
    assert result['permissions'] == ['view', 'edit']
    assert (await db.roles.find_one({'_id': role['_id']}))['permissions'] == ['view', 'edit']
    assert await db.audit_logs.count_documents({'entityId': str(role['_id']), 'action': 'edit'}) == 1
    assert await db.audit_logs.count_documents({'entityId': str(role['_id']), 'action': 'permissionChange'}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('revocation', ['permission', 'actor'])
async def test_concurrent_revocation_forces_retry_and_reauthorization(db, monkeypatch, revocation):
    """A revocation committed after the initial check invalidates the mutation."""
    actor = await make_admin(db)
    repo = AdminRepository(db)
    conflict, committed = asyncio.Event(), asyncio.Event()
    original_guard = repo.guard_admin
    attempts = 0

    async def count_guard(*args):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            await asyncio.wait_for(committed.wait(), 15)
        try:
            return await original_guard(*args)
        except OperationFailure as exc:
            if exc.has_error_label('TransientTransactionError'):
                conflict.set()
            raise

    monkeypatch.setattr(repo, 'guard_admin', count_guard)
    writer = None
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                if revocation == 'permission':
                    await db.roles.update_one({'name': 'Administrator'}, {'$set': {'permissions': ['delete']}}, session=session)
                else:
                    await db.users.update_one({'_id': actor['_id']}, {'$set': {'role': 'Engineer'}}, session=session)
                writer = asyncio.create_task(admin.save_user(repo, actor, user_body()))
                await asyncio.wait_for(conflict.wait(), 10)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(writer, 15)
        assert error.value.status_code == 403
        assert attempts >= 2
        assert await db.users.count_documents({'email': 'user@example.com'}) == 0
        assert await db.audit_logs.count_documents({}) == 0
    finally:
        committed.set()
        if writer:
            if not writer.done():
                writer.cancel()
            await asyncio.wait_for(asyncio.gather(writer, return_exceptions=True), 10)


@pytest.mark.asyncio
async def test_assignment_validation_and_deterministic_guards(db, monkeypatch):
    """Reject duplicate identifiers and lock all destinations in stable order."""
    actor = await make_admin(db)
    ids = [ObjectId(), ObjectId()]
    await db.portfolios.insert_many([{'_id': key, 'name': str(key)} for key in ids])
    original_guard = admin.guard_portfolio
    seen = []

    async def record_guard(database, key, session):
        seen.append(key)
        return await original_guard(database, key, session)

    monkeypatch.setattr(admin, 'guard_portfolio', record_guard)
    with pytest.raises(HTTPException) as error:
        await admin.save_user(AdminRepository(db), actor, user_body(portfolios=[str(ids[0]), str(ids[0]).upper()]))
    assert error.value.status_code == 422
    await admin.save_user(AdminRepository(db), actor, user_body(portfolios=[str(ids[1]), str(ids[0])]))
    assert seen == sorted(ids)


@pytest.mark.asyncio
async def test_standalone_fails_closed():
    """Never persist a user or audit record without transaction support."""
    uri = os.environ.get('TEST_MONGODB_STANDALONE_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_STANDALONE_URI to test fail-closed behavior')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'admin_standalone_' + uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command('hello')
        assert not hello.get('setName') and hello.get('msg') != 'isdbgrid'
        await db.roles.insert_one({'name': 'Administrator', 'permissions': ['administer']})
        actor = await make_admin(db)
        with pytest.raises(OperationFailure):
            await admin.save_user(AdminRepository(db), actor, user_body())
        assert await db.users.count_documents({}) == 1
        assert await db.audit_logs.count_documents({}) == 0
    finally:
        await client.drop_database(name)
        await client.close()
