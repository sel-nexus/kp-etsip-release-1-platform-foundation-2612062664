"""Exercise export authorization against a real MongoDB replica set."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure

from app.admin import service as admin
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.audit import router as audit
from app.audit.router import router as audit_router
from app.core.security import create_token
from app.reports import service as reports
from app.core.database import get_db
from app.reports.router import router as reports_router


@pytest_asyncio.fixture
async def environment():
    """Provide isolated real database and HTTP application fixtures."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI')
    if not uri:
        pytest.fail('Set TEST_MONGODB_REPLICA_URI to a test replica set')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    name = 'export_auth_races_' + uuid4().hex
    db = client[name]
    try:
        assert (await client.admin.command('hello')).get('setName')
        portfolio = (await db.portfolios.insert_one({'name': 'Sensitive'})).inserted_id
        actor = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader',
                 'status': 'Active', 'tokenVersion': 0, 'email': 'leader@example.com', 'portfolioAccess': [str(portfolio)]}
        await db.users.insert_one(actor.copy())
        await db.roles.insert_one({'name': 'Portfolio Leader', 'permissions': ['view', 'export']})
        await db.applications.insert_one({'name': 'Secret', 'portfolioId': str(portfolio)})
        app = FastAPI()
        app.include_router(reports_router)
        app.include_router(audit_router)
        app.dependency_overrides[get_db] = lambda: db
        secret = 'test-signing-key-01234567890123456789'
        app.state.settings = SimpleNamespace(jwt_secret=secret)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test',
                               headers={'Authorization': 'Bearer ' + create_token(actor, secret)}) as http:
            yield SimpleNamespace(db=db, actor=actor, portfolio=portfolio, http=http)
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['applications', 'portfolios'])
async def test_scope_is_refreshed_after_authentication(environment, monkeypatch, kind):
    """A removed assignment cannot leak through the authenticated snapshot."""
    env = environment
    original = reports.export_step
    async def change_before_first_step(db, user, roles, operation, expected_scope=None):
        await env.db.users.update_one({'_id': env.actor['_id']}, {'$set': {'portfolioAccess': []}})
        return await original(db, user, roles, operation, expected_scope)
    monkeypatch.setattr(reports, 'export_step', change_before_first_step)
    response = await env.http.get(f'/api/reports/{kind}?format=csv')
    assert response.status_code == 200
    assert 'Sensitive' not in response.text and 'Secret' not in response.text
    assert await env.db.audit_logs.count_documents({'action': 'export'}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,change', [(kind, change) for kind in ('applications', 'portfolios')
                                         for change in ('export', 'view', 'role', 'inactive', 'token', 'assignment')]
                         + [('audit', change) for change in ('export', 'view', 'role', 'inactive', 'token')])
async def test_revocation_between_export_steps_never_returns_partial_csv(environment, monkeypatch, kind, change):
    """Commit an actual admin change while a multi-step export is paused."""
    env = environment
    role_name = 'Auditor' if kind == 'audit' else 'Portfolio Leader'
    if kind == 'audit':
        await env.db.users.update_one({'_id': env.actor['_id']}, {'$set': {'role': role_name}})
        env.actor['role'] = role_name
        env.http.headers['Authorization'] = 'Bearer ' + create_token(env.actor, 'test-signing-key-01234567890123456789')
        await env.db.roles.insert_one({'name': role_name, 'permissions': ['view', 'export']})
    else:
        ids = [ObjectId() for _ in range(100)]
        await env.db.portfolios.insert_many([{'_id': ident, 'name': f'Portfolio {i:03d}'} for i, ident in enumerate(ids)])
        await env.db.applications.insert_many([{'name': f'App {i:03d}', 'portfolioId': str(ident)} for i, ident in enumerate(ids)])
        await env.db.users.update_one({'_id': env.actor['_id']}, {'$set': {'portfolioAccess': [str(env.portfolio), *map(str, ids)]}})
    administrator = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                     'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
    await env.db.users.insert_one(administrator)
    await env.db.roles.insert_many([{'name': 'Administrator', 'permissions': ['administer', 'view', 'export']},
                                   {'name': 'Read Only', 'permissions': ['view']}])
    role = await env.db.roles.find_one({'name': role_name})
    reached, release = asyncio.Event(), asyncio.Event()
    module = audit if kind == 'audit' else reports
    original = module.export_step
    calls = 0

    async def pause_after_first(*args, **kwargs):
        nonlocal calls
        result = await original(*args, **kwargs)
        calls += 1
        if calls == 1:
            reached.set()
            await asyncio.wait_for(release.wait(), 15)
        return result

    monkeypatch.setattr(module, 'export_step', pause_after_first)
    path = '/api/audit-logs?format=csv' if kind == 'audit' else f'/api/reports/{kind}?format=csv'
    exporting = asyncio.create_task(env.http.get(path))
    try:
        await asyncio.wait_for(reached.wait(), 15)
        repo = AdminRepository(env.db)
        if change in ('view', 'export'):
            await admin.save_role(repo, administrator, RoleInput(name=role_name, permissions=['view' if change == 'export' else 'export']), str(role['_id']))
        elif change == 'inactive':
            await admin.deactivate(repo, administrator, str(env.actor['_id']))
        else:
            access = [] if change == 'assignment' else [str(env.portfolio)]
            body = UserInput(name='Leader', email=env.actor['email'],
                             role='Read Only' if change == 'role' else role_name,
                             portfolioAccess=access,
                             password='Strong-Password123!' if change == 'token' else None)
            await admin.save_user(repo, administrator, body, str(env.actor['_id']))
        release.set()
        response = await asyncio.wait_for(exporting, 15)
        # Authentication and the first guarded step completed before revocation;
        # the following guarded step rejects every change in this scenario.
        assert response.status_code == 403
        assert not response.headers['content-type'].startswith('text/csv')
        assert 'Secret' not in response.text and 'Sensitive' not in response.text
        assert await env.db.audit_logs.count_documents({'action': 'export'}) == 0
        assert await env.db.audit_logs.count_documents({'userId': administrator['_id']}) >= 1
    finally:
        release.set()
        await asyncio.gather(exporting, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['applications', 'portfolios', 'audit'])
async def test_inflight_revocation_serializes_with_export(environment, monkeypatch, kind):
    """A revocation holding a role write prevents a concurrent export from succeeding."""
    env = environment
    role_name = 'Auditor' if kind == 'audit' else 'Portfolio Leader'
    if kind == 'audit':
        await env.db.users.update_one({'_id': env.actor['_id']}, {'$set': {'role': role_name}})
        env.actor['role'] = role_name
        env.http.headers['Authorization'] = 'Bearer ' + create_token(env.actor, 'test-signing-key-01234567890123456789')
        await env.db.roles.insert_one({'name': role_name, 'permissions': ['view', 'export']})
    administrator = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                     'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
    await env.db.users.insert_one(administrator)
    await env.db.roles.insert_one({'name': 'Administrator', 'permissions': ['administer']})
    role = await env.db.roles.find_one({'name': role_name})
    repo = AdminRepository(env.db)
    original_audit = repo.audit
    original_transact = audit.transact
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    attempts = 0

    async def pause_revocation(*args, **kwargs):
        await original_audit(*args, **kwargs)
        reached.set()
        await asyncio.wait_for(release.wait(), 15)

    async def count_export_transactions(db, callback):
        async def counted(session):
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
        return await original_transact(db, counted)

    monkeypatch.setattr(repo, 'audit', pause_revocation)
    monkeypatch.setattr(audit, 'transact', count_export_transactions)
    revocation = asyncio.create_task(admin.save_role(repo, administrator,
        RoleInput(name=role_name, permissions=['view']), str(role['_id'])))
    exporting = None
    try:
        await asyncio.wait_for(reached.wait(), 15)
        path = '/api/audit-logs?format=csv' if kind == 'audit' else f'/api/reports/{kind}?format=csv'
        exporting = asyncio.create_task(env.http.get(path))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(revocation, 15)
        committed.set()
        response = await asyncio.wait_for(exporting, 15)
        assert response.status_code == 403
        assert not response.headers['content-type'].startswith('text/csv')
        assert attempts >= 2
        assert await env.db.audit_logs.count_documents({'action': 'export'}) == 0
        assert await env.db.audit_logs.count_documents({'action': 'permissionChange'}) == 1
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (revocation, exporting) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['applications', 'portfolios', 'audit'])
async def test_export_commit_serializes_before_competing_revocation(environment, monkeypatch, kind):
    """An authorized final audit commits before a competing role revocation."""
    env = environment
    role_name = 'Auditor' if kind == 'audit' else 'Portfolio Leader'
    if kind == 'audit':
        await env.db.users.update_one({'_id': env.actor['_id']}, {'$set': {'role': role_name}})
        env.actor['role'] = role_name
        env.http.headers['Authorization'] = 'Bearer ' + create_token(env.actor, 'test-signing-key-01234567890123456789')
        await env.db.roles.insert_one({'name': role_name, 'permissions': ['view', 'export']})
    administrator = {'_id': ObjectId(), 'name': 'Admin', 'email': 'admin@example.com',
                     'role': 'Administrator', 'status': 'Active', 'tokenVersion': 0}
    await env.db.users.insert_one(administrator)
    await env.db.roles.insert_one({'name': 'Administrator', 'permissions': ['administer']})
    role = await env.db.roles.find_one({'name': role_name})
    repo = AdminRepository(env.db)
    original_audit = AdminRepository.audit
    original_transact = admin.transact
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    attempts = 0

    async def pause_export(self, actor, action, *args, **kwargs):
        await original_audit(self, actor, action, *args, **kwargs)
        if action == 'export':
            reached.set()
            await asyncio.wait_for(release.wait(), 15)

    async def count_revocation_transactions(db, callback):
        async def counted(session):
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
        return await original_transact(db, counted)

    monkeypatch.setattr(AdminRepository, 'audit', pause_export)
    monkeypatch.setattr(admin, 'transact', count_revocation_transactions)
    path = '/api/audit-logs?format=csv' if kind == 'audit' else f'/api/reports/{kind}?format=csv'
    exporting = asyncio.create_task(env.http.get(path))
    revocation = None
    try:
        await asyncio.wait_for(reached.wait(), 15)
        revocation = asyncio.create_task(admin.save_role(repo, administrator,
            RoleInput(name=role_name, permissions=['view']), str(role['_id'])))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        response = await asyncio.wait_for(exporting, 15)
        committed.set()
        await asyncio.wait_for(revocation, 15)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/csv')
        assert attempts >= 2
        assert await env.db.audit_logs.count_documents({'action': 'export'}) == 1
        assert await env.db.audit_logs.count_documents({'action': 'permissionChange'}) == 1
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (exporting, revocation) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
async def test_inconsistent_page_is_rejected_without_success_audit(environment):
    """A disappearing later page cannot yield a partial export."""
    env = environment
    offsets = []
    async def page(live, size, offset):
        offsets.append(offset)
        return {'items': [{'name': 'First'}] if not offset else [], 'total': 2}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await reports.export_csv(env.db, env.actor, 'applications', page)
    assert error.value.status_code == 409
    assert offsets == [0, 1]
    assert await env.db.audit_logs.count_documents({'action': 'export'}) == 0


@pytest.mark.asyncio
async def test_page_failure_does_not_audit_export(environment):
    """Propagate a page failure without producing an export event."""
    async def page(live, size, offset):
        raise RuntimeError('page unavailable')
    with pytest.raises(RuntimeError, match='page unavailable'):
        await reports.export_csv(environment.db, environment.actor, 'applications', page)
    assert await environment.db.audit_logs.count_documents({'action': 'export'}) == 0
