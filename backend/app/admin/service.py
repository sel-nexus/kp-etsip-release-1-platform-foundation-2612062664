"""Apply administrator authorization and account management rules."""

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.auth.service import public_user
from app.core.security import hash_password
from app.core.transactions import guard_portfolio, transact


def ident(value: str) -> ObjectId:
    """Reject malformed or missing identifiers as not found."""
    if not ObjectId.is_valid(value):
        raise HTTPException(404, 'Resource not found')
    return ObjectId(value)


async def authorize(repo: AdminRepository, user: dict, permission: str, roles: set[str]) -> None:
    """Intersect live permissions with fixed role restrictions."""
    if user['role'] not in roles or permission not in await repo.permissions(user['role']):
        raise HTTPException(403, 'Not permitted')


def present_user(doc: dict) -> dict:
    """Return account fields without credential material."""
    return {**public_user(doc), 'managerName': doc.get('managerName'), 'vendorIndicator': doc.get('vendorIndicator', False), 'lastLogin': doc.get('lastLogin'), 'authSource': 'local'}


def present_role(doc: dict) -> dict:
    """Return the public role representation."""
    return {'id': str(doc['_id']), 'name': doc['name'], 'description': doc.get('description', ''), 'permissions': doc.get('permissions', [])}


async def get_doc(repo: AdminRepository, collection: str, value: str) -> dict:
    """Find a document or return a not-found response."""
    doc = await repo.get(collection, ident(value))
    if not doc:
        raise HTTPException(404, 'Resource not found')
    return doc


async def save_user(repo: AdminRepository, actor: dict, body: UserInput, value: str | None = None) -> dict:
    """Create or update an account and audit its changes."""
    await authorize(repo, actor, 'administer', {'Administrator'})
    key = ident(value) if value else None
    if any(not ObjectId.is_valid(v) for v in body.portfolioAccess):
        raise HTTPException(422, 'Portfolio assignments must reference distinct existing portfolios')
    destinations = sorted(ObjectId(v) for v in body.portfolioAccess)
    if len(set(destinations)) != len(destinations):
        raise HTTPException(422, 'Portfolio assignments must reference distinct existing portfolios')
    password_hash = await run_in_threadpool(hash_password, body.password) if body.password else None

    async def operation(session):
        """Recheck all mutable state and persist one retryable change."""
        live_actor = await repo.guard_admin(actor, session)
        if not live_actor or actor.get('role') != 'Administrator':
            raise HTTPException(403, 'Not permitted')
        old = await repo.get('users', key, session) if key else None
        if key and not old:
            raise HTTPException(404, 'Resource not found')
        for destination in destinations:
            if not await guard_portfolio(repo.db, destination, session):
                raise HTTPException(422, 'Portfolio assignments must reference distinct existing portfolios')
        if not await repo.db.roles.find_one({'name': body.role}, session=session):
            raise HTTPException(422, 'Role does not exist')
        if not old and not password_hash:
            raise HTTPException(422, 'Password is required')
        if old and old['role'] == 'Administrator' and old['status'] == 'Active' and (body.role != 'Administrator' or body.status != 'Active') and await repo.active_admin_count(session) <= 1:
            raise HTTPException(409, 'Cannot remove the last active administrator')
        values = body.model_dump(exclude={'password'})
        values['portfolioAccess'] = [str(ObjectId(v)) for v in body.portfolioAccess]
        if password_hash:
            values['passwordHash'] = password_hash
            values['mustChangePassword'] = True
        if old and (body.password or (old['status'] == 'Active' and body.status == 'Inactive')):
            values['tokenVersion'] = old.get('tokenVersion', 0) + 1
        if old:
            await repo.update('users', old['_id'], values, session)
            result_key = old['_id']
        else:
            values.update({'lastLogin': None, 'authSource': 'local', 'mustChangePassword': True, 'tokenVersion': 0, 'defaultLandingPage': 'dashboard' if body.role in ('Administrator', 'Executive') else 'applications'})
            result_key = await repo.insert('users', values, session)
        await repo.audit(live_actor, 'edit' if old else 'create', 'user', result_key, 'User updated' if old else 'User created', session)
        if old and old['role'] != body.role:
            await repo.audit(live_actor, 'roleChange', 'user', result_key, 'User role changed', session)
        return present_user(await repo.get('users', result_key, session))

    try:
        return await transact(repo.db, operation)
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Email already exists') from exc


async def deactivate(repo: AdminRepository, actor: dict, value: str) -> None:
    """Deactivate an account while retaining historical references."""
    await authorize(repo, actor, 'administer', {'Administrator'})
    key = ident(value)

    async def operation(session):
        """Serialize the count and commit deactivation with its event."""
        live_actor = await repo.guard_admin(actor, session)
        if not live_actor or actor.get('role') != 'Administrator':
            raise HTTPException(403, 'Not permitted')
        old = await repo.get('users', key, session)
        if not old:
            raise HTTPException(404, 'Resource not found')
        if old['role'] == 'Administrator' and old['status'] == 'Active' and await repo.active_admin_count(session) <= 1:
            raise HTTPException(409, 'Cannot remove the last active administrator')
        await repo.update('users', old['_id'], {'status': 'Inactive', 'tokenVersion': old.get('tokenVersion', 0) + 1}, session)
        await repo.audit(live_actor, 'delete', 'user', old['_id'], 'User deactivated', session)

    await transact(repo.db, operation)


async def save_role(repo: AdminRepository, actor: dict, body: RoleInput, value: str | None = None) -> dict:
    """Create or update a supported role without changing its identity."""
    await authorize(repo, actor, 'administer', {'Administrator'})
    key = ident(value) if value else None

    async def operation(session):
        """Persist role fields and permission events in the same transaction."""
        live_actor = await repo.guard_admin(actor, session)
        if not live_actor or actor.get('role') != 'Administrator':
            raise HTTPException(403, 'Not permitted')
        old = await repo.get('roles', key, session) if key else None
        if key and not old:
            raise HTTPException(404, 'Resource not found')
        if old and old['name'] != body.name:
            raise HTTPException(409, 'Role names cannot be changed')
        if body.name == 'Administrator' and 'administer' not in body.permissions:
            raise HTTPException(409, 'Administrator must retain administer permission')
        if old:
            await repo.update('roles', old['_id'], body.model_dump(), session)
            result_key = old['_id']
        else:
            result_key = await repo.insert('roles', body.model_dump(), session)
        await repo.audit(live_actor, 'edit' if old else 'create', 'role', result_key, 'Role updated' if old else 'Role created', session)
        if not old or set(old.get('permissions', [])) != set(body.permissions):
            await repo.audit(live_actor, 'permissionChange', 'role', result_key, 'Role permissions changed', session)
        return present_role(await repo.get('roles', result_key, session))

    try:
        return await transact(repo.db, operation)
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Role already exists') from exc
