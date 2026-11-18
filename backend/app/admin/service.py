"""Apply administrator authorization and account management rules."""

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.auth.service import public_user
from app.core.security import hash_password


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
    old = await get_doc(repo, 'users', value) if value else None
    if not await repo.valid_portfolios(body.portfolioAccess):
        raise HTTPException(422, 'Portfolio assignments must reference distinct existing portfolios')
    if not await repo.db.roles.find_one({'name': body.role}):
        raise HTTPException(422, 'Role does not exist')
    if not old and not body.password:
        raise HTTPException(422, 'Password is required')
    if old and old['role'] == 'Administrator' and old['status'] == 'Active' and (body.role != 'Administrator' or body.status != 'Active') and await repo.active_admin_count() <= 1:
        raise HTTPException(409, 'Cannot remove the last active administrator')
    values = body.model_dump(exclude={'password'})
    if body.password:
        values['passwordHash'] = await run_in_threadpool(hash_password, body.password)
        values['mustChangePassword'] = True
    if old and (body.password or (old['status'] == 'Active' and body.status == 'Inactive')):
        values['tokenVersion'] = old.get('tokenVersion', 0) + 1
    try:
        if old:
            await repo.update('users', old['_id'], values)
            key = old['_id']
        else:
            values.update({'lastLogin': None, 'authSource': 'local', 'mustChangePassword': True, 'tokenVersion': 0, 'defaultLandingPage': 'dashboard' if body.role in ('Administrator', 'Executive') else 'applications'})
            key = await repo.insert('users', values)
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Email already exists') from exc
    await repo.audit(actor, 'edit' if old else 'create', 'user', key, 'User updated' if old else 'User created')
    if old and old['role'] != body.role:
        await repo.audit(actor, 'roleChange', 'user', key, 'User role changed')
    return present_user(await repo.get('users', key))


async def deactivate(repo: AdminRepository, actor: dict, value: str) -> None:
    """Deactivate an account while retaining historical references."""
    await authorize(repo, actor, 'administer', {'Administrator'})
    old = await get_doc(repo, 'users', value)
    if old['role'] == 'Administrator' and old['status'] == 'Active' and await repo.active_admin_count() <= 1:
        raise HTTPException(409, 'Cannot remove the last active administrator')
    await repo.update('users', old['_id'], {'status': 'Inactive', 'tokenVersion': old.get('tokenVersion', 0) + 1})
    await repo.audit(actor, 'delete', 'user', old['_id'], 'User deactivated')


async def save_role(repo: AdminRepository, actor: dict, body: RoleInput, value: str | None = None) -> dict:
    """Create or update a supported role without changing its identity."""
    await authorize(repo, actor, 'administer', {'Administrator'})
    old = await get_doc(repo, 'roles', value) if value else None
    if old and old['name'] != body.name:
        raise HTTPException(409, 'Role names cannot be changed')
    if body.name == 'Administrator' and 'administer' not in body.permissions:
        raise HTTPException(409, 'Administrator must retain administer permission')
    try:
        if old:
            await repo.update('roles', old['_id'], body.model_dump())
            key = old['_id']
        else:
            key = await repo.insert('roles', body.model_dump())
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Role already exists') from exc
    await repo.audit(actor, 'edit' if old else 'create', 'role', key, 'Role updated' if old else 'Role created')
    if not old or set(old.get('permissions', [])) != set(body.permissions):
        await repo.audit(actor, 'permissionChange', 'role', key, 'Role permissions changed')
    return present_role(await repo.get('roles', key))
