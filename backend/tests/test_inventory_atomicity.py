"""Exercise inventory transactions on a disposable replica-set database.

Set TEST_MONGODB_REPLICA_URI to override the local replica set default.
Each test creates and drops only its own randomly named database.
"""

import asyncio
import os
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
import pytest
import pytest_asyncio

from app.applications import service as applications
from app.applications.repository import ApplicationRepository
from app.applications.schemas import ApplicationInput
from app.core.transactions import guard_portfolio, transact
from app.portfolios import service as portfolios
from app.portfolios.repository import PortfolioRepository
from app.portfolios.schemas import PortfolioInput


@pytest_asyncio.fixture
async def db():
    """Provide a unique real replica-set database and clean it up."""
    uri = os.environ.get("TEST_MONGODB_REPLICA_URI", "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = "inventory_atomicity_" + uuid4().hex
    database = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName"), "TEST_MONGODB_REPLICA_URI must target a replica set"
        await database.roles.insert_one({"name": "Administrator", "permissions": ["view", "create", "edit", "delete"]})
        await database.portfolios.create_index("name", unique=True)
        await database.applications.create_index("name", unique=True)
        yield database
    finally:
        await client.drop_database(name)
        await client.close()


@pytest_asyncio.fixture
async def admin(db):
    """Persist an active administrator with a valid session version."""
    actor = {"_id": ObjectId(), "name": "Admin", "email": "admin@example.com",
             "role": "Administrator", "status": "Active", "tokenVersion": 0,
             "mustChangePassword": False, "portfolioAccess": []}
    await db.users.insert_one(actor.copy())
    return actor


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,action", [(k, a) for k in ("portfolio", "application") for a in ("create", "edit", "delete")])
async def test_audit_failure_rolls_back_mutation_and_audit(db, admin, monkeypatch, kind, action):
    """A failure after audit insertion aborts both writes, including the guard."""
    pid = (await db.portfolios.insert_one({"name": "Original", "owner": "Old"})).inserted_id
    app_id = (await db.applications.insert_one({"name": "Original app", "portfolioId": str(pid)})).inserted_id
    if kind == "portfolio" and action == "delete":
        await db.applications.delete_one({"_id": app_id})
    before_portfolios = await db.portfolios.find().to_list(None)
    before_apps = await db.applications.find().to_list(None)
    repo = PortfolioRepository(db) if kind == "portfolio" else ApplicationRepository(db)
    original_audit = repo.audit

    async def fail_after_audit(*args):
        await original_audit(*args)
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(repo, "audit", fail_after_audit)
    if kind == "portfolio":
        body = PortfolioInput(name="New", owner="New") if action != "delete" else None
        call = portfolios.mutate(repo, admin, action, None if action == "create" else str(pid), body)
    else:
        body = ApplicationInput(name="New app", portfolioId=str(pid)) if action != "delete" else None
        call = applications.mutate(repo, admin, action, None if action == "create" else str(app_id), body)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        await call
    assert await db.portfolios.find().to_list(None) == before_portfolios
    assert await db.applications.find().to_list(None) == before_apps
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_deletion_rejects_any_user_assignment(db, admin):
    """Even inactive users prevent deletion, and a rejection is not audited."""
    pid = (await db.portfolios.insert_one({"name": "Assigned"})).inserted_id
    await db.users.insert_one({"status": "Inactive", "portfolioAccess": [str(pid)]})
    with pytest.raises(HTTPException) as error:
        await portfolios.mutate(PortfolioRepository(db), admin, "delete", str(pid))
    assert error.value.status_code == 409
    assert await db.portfolios.find_one({"_id": pid}) == {"_id": pid, "name": "Assigned"}
    assert await db.audit_logs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "edit"])
async def test_reference_wins_against_concurrent_delete(db, admin, monkeypatch, action):
    """A create or move makes delete retry through their shared admin guard."""
    target = (await db.portfolios.insert_one({"name": "Target"})).inserted_id
    source = (await db.portfolios.insert_one({"name": "Source"})).inserted_id
    app = (await db.applications.insert_one({"name": "Existing", "portfolioId": str(source)})).inserted_id
    app_repo = ApplicationRepository(db)
    portfolio_repo = PortfolioRepository(db)
    reached = asyncio.Event()
    release = asyncio.Event()
    original_audit = app_repo.audit
    original_transact = portfolios.transact
    conflict = asyncio.Event()
    committed = asyncio.Event()
    attempts = 0

    async def count_transactions(database, callback):
        async def count(session):
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                await asyncio.wait_for(committed.wait(), 15)
            try:
                return await callback(session)
            except OperationFailure as exc:
                if exc.has_error_label("TransientTransactionError"):
                    conflict.set()
                raise
        return await original_transact(database, count)

    async def pause_audit(*args):
        await original_audit(*args)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    monkeypatch.setattr(app_repo, "audit", pause_audit)
    monkeypatch.setattr(portfolios, "transact", count_transactions)
    body = ApplicationInput(name="New" if action == "create" else "Existing", portfolioId=str(target))
    writer = asyncio.create_task(applications.mutate(app_repo, admin, action, None if action == "create" else str(app), body))
    deleter = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        deleter = asyncio.create_task(portfolios.mutate(portfolio_repo, admin, "delete", str(target)))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(writer, 15)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(deleter, 15)
        assert error.value.status_code == 409
        assert attempts >= 2
        assert await db.portfolios.find_one({"_id": target}) is not None
        assert await db.applications.count_documents({"portfolioId": str(target)}) == 1
        assert await db.audit_logs.count_documents({"entityType": "application", "action": action}) == 1
        assert await db.audit_logs.count_documents({"entityType": "portfolio", "action": "delete"}) == 0
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, deleter) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "edit"])
async def test_delete_wins_against_concurrent_reference(db, admin, monkeypatch, action):
    """A delete makes create or move retry through their shared admin guard."""
    target = (await db.portfolios.insert_one({"name": "Target"})).inserted_id
    source = (await db.portfolios.insert_one({"name": "Source"})).inserted_id
    app = (await db.applications.insert_one({"name": "Existing", "portfolioId": str(source)})).inserted_id
    app_repo = ApplicationRepository(db)
    portfolio_repo = PortfolioRepository(db)
    reached = asyncio.Event()
    release = asyncio.Event()
    original_audit = portfolio_repo.audit
    original_transact = applications.transact
    conflict = asyncio.Event()
    committed = asyncio.Event()
    attempts = 0

    async def count_transactions(database, callback):
        async def count(session):
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                await asyncio.wait_for(committed.wait(), 15)
            try:
                return await callback(session)
            except OperationFailure as exc:
                if exc.has_error_label("TransientTransactionError"):
                    conflict.set()
                raise
        return await original_transact(database, count)

    async def pause_audit(*args):
        await original_audit(*args)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    monkeypatch.setattr(portfolio_repo, "audit", pause_audit)
    monkeypatch.setattr(applications, "transact", count_transactions)
    deleter = asyncio.create_task(portfolios.mutate(portfolio_repo, admin, "delete", str(target)))
    writer = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        body = ApplicationInput(name="New" if action == "create" else "Existing", portfolioId=str(target))
        writer = asyncio.create_task(applications.mutate(app_repo, admin, action, None if action == "create" else str(app), body))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(deleter, 15)
        committed.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(writer, 15)
        assert error.value.status_code == 404
        assert attempts >= 2
        assert await db.portfolios.find_one({"_id": target}) is None
        assert await db.applications.count_documents({"portfolioId": str(target)}) == 0
        assert (await db.applications.find_one({"_id": app}))["portfolioId"] == str(source)
        assert await db.audit_logs.count_documents({"entityType": "portfolio", "action": "delete"}) == 1
        assert await db.audit_logs.count_documents({"entityType": "application"}) == 0
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (writer, deleter) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["reference", "delete"])
async def test_portfolio_guard_conflicts_across_independent_sessions(db, winner):
    """The portfolio guard itself serializes reference creation and deletion."""
    target = (await db.portfolios.insert_one({"name": "Target"})).inserted_id
    reached, release, conflict, committed = (asyncio.Event() for _ in range(4))
    attempts = 0

    async def winning_operation(session):
        assert await guard_portfolio(db, target, session)
        if winner == "reference":
            await db.applications.insert_one({"name": "Reference", "portfolioId": str(target)}, session=session)
        else:
            await db.portfolios.delete_one({"_id": target}, session=session)
        reached.set()
        await asyncio.wait_for(release.wait(), 15)

    async def competing_operation(session):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            await asyncio.wait_for(committed.wait(), 15)
        try:
            exists = await guard_portfolio(db, target, session)
        except OperationFailure as exc:
            if exc.has_error_label("TransientTransactionError"):
                conflict.set()
            raise
        if not exists:
            return "missing"
        if winner == "reference":
            if await db.applications.find_one({"portfolioId": str(target)}, session=session):
                return "referenced"
            await db.portfolios.delete_one({"_id": target}, session=session)
            return "deleted"
        await db.applications.insert_one({"name": "Contender", "portfolioId": str(target)}, session=session)
        return "created"

    first = asyncio.create_task(transact(db, winning_operation))
    second = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        second = asyncio.create_task(transact(db, competing_operation))
        await asyncio.wait_for(conflict.wait(), 10)
        release.set()
        await asyncio.wait_for(first, 15)
        committed.set()
        assert await asyncio.wait_for(second, 15) == ("referenced" if winner == "reference" else "missing")
        assert attempts >= 2
        assert (await db.portfolios.find_one({"_id": target}) is not None) == (winner == "reference")
        assert await db.applications.count_documents({"portfolioId": str(target)}) == (1 if winner == "reference" else 0)
    finally:
        release.set()
        committed.set()
        tasks = [task for task in (first, second) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["portfolio", "application"])
async def test_standalone_fails_closed(kind):
    """No inventory or audit write may escape when transactions are unavailable."""
    uri = os.environ.get("TEST_MONGODB_STANDALONE_URI")
    if not uri:
        pytest.fail("Set TEST_MONGODB_STANDALONE_URI to test fail-closed behavior")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    name = "inventory_standalone_" + uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert not hello.get("setName") and hello.get("msg") != "isdbgrid"
        await db.roles.insert_one({"name": "Administrator", "permissions": ["create"]})
        admin = {"_id": ObjectId(), "name": "Admin", "email": "admin@example.com",
                 "role": "Administrator", "status": "Active", "tokenVersion": 0,
                 "mustChangePassword": False, "portfolioAccess": []}
        await db.users.insert_one(admin.copy())
        pid = (await db.portfolios.insert_one({"name": "Existing"})).inserted_id
        if kind == "portfolio":
            call = portfolios.mutate(PortfolioRepository(db), admin, "create", body=PortfolioInput(name="New"))
        else:
            call = applications.mutate(ApplicationRepository(db), admin, "create", body=ApplicationInput(name="New", portfolioId=str(pid)))
        with pytest.raises(OperationFailure):
            await call
        assert await db.portfolios.count_documents({}) == 1
        assert await db.applications.count_documents({}) == 0
        assert await db.audit_logs.count_documents({}) == 0
    finally:
        await client.drop_database(name)
        await client.close()
