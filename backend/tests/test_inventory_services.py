"""Exercise inventory service branches against an isolated real MongoDB."""

import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
import pytest
import pytest_asyncio

from app.applications import service as applications
from app.applications.repository import ApplicationRepository
from app.applications.schemas import ApplicationInput
from app.portfolios import service as portfolios
from app.portfolios.repository import PortfolioRepository
from app.portfolios.schemas import PortfolioInput


@pytest_asyncio.fixture
async def inventory_db():
    """Initialize a unique database and remove only that database afterward."""
    uri = os.environ.get('TEST_MONGODB_REPLICA_URI', 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true')
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = 'ketsip_inventory_service_test_' + uuid4().hex
    db = client[name]
    try:
        assert (await client.admin.command('hello')).get('setName'), 'TEST_MONGODB_REPLICA_URI must target a replica set'
        await db.roles.insert_many([
            {'name': 'Administrator', 'permissions': ['view', 'create', 'edit', 'delete']},
            {'name': 'Portfolio Leader', 'permissions': ['view', 'create', 'edit']},
            {'name': 'Engineer', 'permissions': ['view', 'create', 'edit', 'delete']},
            {'name': 'Read Only', 'permissions': ['view', 'create', 'edit', 'delete']},
        ])
        await db.portfolios.create_index('name', unique=True)
        await db.applications.create_index('name', unique=True)
        yield db
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
async def test_portfolio_service_branches_and_persistence(inventory_db):
    db = inventory_db
    repo = PortfolioRepository(db)
    admin = {'_id': ObjectId(), 'name': 'Admin', 'role': 'Administrator'}
    leader = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader', 'portfolioAccess': []}
    engineer = {'_id': ObjectId(), 'name': 'Engineer', 'role': 'Engineer', 'portfolioAccess': []}
    for user in (admin, leader, engineer):
        user.update(status='Active', tokenVersion=0, mustChangePassword=False,
                    email=f"{user['role'].replace(' ', '-').lower()}@example.com")
    await db.users.insert_many([user.copy() for user in (admin, leader, engineer)])
    body = PortfolioInput(name='Service portfolio', owner='Owner')
    created = await portfolios.mutate(repo, admin, 'create', body=body)
    ident = ObjectId(created['id'])
    assert (await db.portfolios.find_one({'_id': ident}))['owner'] == 'Owner'
    assert created['applicationCount'] == 0 and created['qualityScore'] is None
    assert created['canEdit'] is True and created['canDelete'] is True
    leader['portfolioAccess'] = [str(ident), 'invalid', 12]
    await db.users.update_one({'_id': leader['_id']}, {'$set': {'portfolioAccess': [str(ident)]}})
    assert portfolios.scope(leader) == [ident]
    assert portfolios.scope(admin) is None
    assert (await portfolios.detail(repo, leader, str(ident)))['canEdit'] is True
    page = await portfolios.list_portfolios(repo, leader, 'Service', 'Active', '', 10, 0)
    assert page['total'] == 1 and page['items'][0]['id'] == str(ident)
    assert page['canCreate'] is False
    outside = (await db.portfolios.insert_one({'name': 'Outside'})).inserted_id
    with pytest.raises(HTTPException) as error:
        await portfolios.detail(repo, leader, str(outside))
    assert error.value.status_code == 403
    assert (await portfolios.detail(repo, admin, str(outside)))['canEdit'] is True
    updated = await portfolios.mutate(repo, leader, 'edit', str(ident), PortfolioInput(name=body.name, owner='New owner'))
    assert updated['owner'] == 'New owner'
    before = await db.portfolios.find_one({'_id': ident})
    assert before['owner'] == 'New owner'
    events = await db.audit_logs.count_documents({'entityType': 'portfolio'})
    for user, action in ((engineer, 'edit'), (leader, 'delete')):
        with pytest.raises(HTTPException) as error:
            await portfolios.mutate(repo, user, action, str(ident), body)
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await portfolios.mutate(repo, admin, 'create', body=body)
    assert error.value.status_code == 409
    for value in ('not-an-id', str(ObjectId())):
        with pytest.raises(HTTPException) as error:
            await portfolios.detail(repo, admin, value)
        assert error.value.status_code == 404
    await db.applications.insert_one({'name': 'Linked', 'portfolioId': str(ident), 'qualityScore': 81})
    with pytest.raises(HTTPException) as error:
        await portfolios.mutate(repo, admin, 'delete', str(ident))
    assert error.value.status_code == 409
    detail = await portfolios.detail(repo, admin, str(ident))
    assert detail['applicationCount'] == 1 and detail['qualityScore'] == 81
    assert await db.portfolios.find_one({'_id': ident}) == before
    assert await db.audit_logs.count_documents({'entityType': 'portfolio'}) == events
    await db.applications.delete_one({'name': 'Linked'})
    await db.users.update_one({'_id': leader['_id']}, {'$set': {'portfolioAccess': []}})
    assert await portfolios.mutate(repo, admin, 'delete', str(ident)) is None
    assert await db.portfolios.find_one({'_id': ident}) is None
    assert await db.audit_logs.count_documents({'entityType': 'portfolio', 'entityId': str(ident)}) == 3


@pytest.mark.asyncio
async def test_application_service_scope_conflicts_and_persistence(inventory_db):
    db = inventory_db
    repo = ApplicationRepository(db)
    first, second = ObjectId(), ObjectId()
    await db.portfolios.insert_many([{'_id': first, 'name': 'First'}, {'_id': second, 'name': 'Second'}])
    admin = {'_id': ObjectId(), 'name': 'Admin', 'role': 'Administrator'}
    leader = {'_id': ObjectId(), 'name': 'Leader', 'role': 'Portfolio Leader', 'portfolioAccess': [str(first), 'invalid', 12]}
    readonly = {'_id': ObjectId(), 'name': 'Reader', 'role': 'Read Only', 'portfolioAccess': [str(first)]}
    for user in (admin, leader, readonly):
        user.update(status='Active', tokenVersion=0, mustChangePassword=False,
                    email=f"{user['role'].replace(' ', '-').lower()}@example.com")
    await db.users.insert_many([admin.copy(), {**leader, 'portfolioAccess': [str(first)]}, readonly.copy()])
    assert applications.scope(admin) is None
    assert applications.scope(leader) == [str(first)]
    body = ApplicationInput(name='Service app', portfolioId=str(first), qualityScore=42)
    created = await applications.mutate(repo, leader, 'create', body=body)
    ident = ObjectId(created['id'])
    assert created['portfolioName'] == 'First' and created['canEdit'] is True
    assert (await db.applications.find_one({'_id': ident}))['qualityScore'] == 42
    page = await applications.listing(repo, leader, 'Service', None, None, None, None, 10, 0)
    assert page['total'] == 1 and page['items'][0]['id'] == str(ident)
    assert page['canCreate'] is True
    other = await applications.mutate(repo, admin, 'create', body=ApplicationInput(name='Outside app', portfolioId=str(second)))
    with pytest.raises(HTTPException) as error:
        await applications.detail(repo, leader, other['id'])
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await applications.mutate(repo, leader, 'edit', other['id'], body)
    assert error.value.status_code == 403
    assert (await db.applications.find_one({'_id': ObjectId(other['id'])}))['portfolioId'] == str(second)
    outside_page = await applications.listing(repo, leader, '', str(second), None, None, None, 10, 0)
    assert outside_page['total'] == 0 and outside_page['items'] == []
    changed = ApplicationInput(name='Service app', portfolioId=str(first), qualityScore=70)
    assert (await applications.mutate(repo, leader, 'edit', str(ident), changed))['qualityScore'] == 70
    before = await db.applications.find_one({'_id': ident})
    assert before['qualityScore'] == 70
    events = await db.audit_logs.count_documents({'entityType': 'application'})
    for user, action, value, input_body, status in [
        (readonly, 'create', None, body, 403),
        (leader, 'delete', str(ident), None, 403),
        (leader, 'edit', str(ident), ApplicationInput(name='Service app', portfolioId=str(second)), 403),
        (admin, 'create', None, ApplicationInput(name='Missing', portfolioId=str(ObjectId())), 404),
        (admin, 'create', None, body, 409),
        (admin, 'edit', str(ObjectId()), body, 404),
        (admin, 'create', None, None, 422),
    ]:
        with pytest.raises(HTTPException) as error:
            await applications.mutate(repo, user, action, value, input_body)
        assert error.value.status_code == status
    for value in ('invalid', str(ObjectId())):
        with pytest.raises(HTTPException) as error:
            await applications.detail(repo, admin, value)
        assert error.value.status_code == 404
    assert await db.applications.find_one({'_id': ident}) == before
    assert await db.audit_logs.count_documents({'entityType': 'application'}) == events
    await db.roles.update_one({'name': 'Portfolio Leader'}, {'$set': {'permissions': ['view']}})
    assert (await applications.detail(repo, leader, str(ident)))['canEdit'] is False
    with pytest.raises(HTTPException) as error:
        await applications.mutate(repo, leader, 'edit', str(ident), changed)
    assert error.value.status_code == 403
    assert await db.applications.find_one({'_id': ident}) == before
    assert await applications.mutate(repo, admin, 'delete', str(ident)) is None
    assert await db.applications.find_one({'_id': ident}) is None
    assert await db.audit_logs.count_documents({'entityType': 'application', 'entityId': str(ident)}) == 3
