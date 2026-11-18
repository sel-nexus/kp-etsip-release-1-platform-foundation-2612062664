"""Expose administrator-only user and role endpoints."""

from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response
from pymongo.asynchronous.database import AsyncDatabase
from app.admin.repository import AdminRepository
from app.admin.schemas import RoleInput, UserInput
from app.admin.service import authorize, deactivate, get_doc, present_role, present_user, save_role, save_user
from app.auth.router import unrestricted_user
from app.core.database import get_db

router = APIRouter(prefix='/api', tags=['Administration'])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


@router.get('/admin/portfolios')
async def assignment_portfolios(db: Db, user: User, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List portfolio identifiers and names for access assignment."""
    repo = AdminRepository(db)
    await authorize(repo, user, 'administer', {'Administrator'})
    page = await repo.page('portfolios', {}, limit, offset)
    return {'items': [{'id': str(doc['_id']), 'name': doc['name']} for doc in page['items']], 'total': page['total']}


@router.get('/users')
async def users(db: Db, user: User, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List accounts for administrators."""
    repo = AdminRepository(db)
    await authorize(repo, user, 'administer', {'Administrator'})
    page = await repo.page('users', {}, limit, offset)
    return {'items': [present_user(doc) for doc in page['items']], 'total': page['total']}


@router.post('/users', status_code=201)
async def create_user(body: UserInput, db: Db, user: User) -> dict:
    """Create a local account."""
    return await save_user(AdminRepository(db), user, body)


@router.get('/users/{user_id}')
async def get_user(user_id: str, db: Db, user: User) -> dict:
    """Read one public account."""
    repo = AdminRepository(db)
    await authorize(repo, user, 'administer', {'Administrator'})
    return present_user(await get_doc(repo, 'users', user_id))


@router.put('/users/{user_id}')
async def update_user(user_id: str, body: UserInput, db: Db, user: User) -> dict:
    """Update a local account."""
    return await save_user(AdminRepository(db), user, body, user_id)


@router.delete('/users/{user_id}', status_code=204)
async def delete_user(user_id: str, db: Db, user: User) -> Response:
    """Deactivate rather than remove an account."""
    await deactivate(AdminRepository(db), user, user_id)
    return Response(status_code=204)


@router.get('/roles')
async def roles(db: Db, user: User, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List supported roles for administrators."""
    repo = AdminRepository(db)
    await authorize(repo, user, 'administer', {'Administrator'})
    page = await repo.page('roles', {}, limit, offset)
    return {'items': [present_role(doc) for doc in page['items']], 'total': page['total']}


@router.post('/roles', status_code=201)
async def create_role(body: RoleInput, db: Db, user: User) -> dict:
    """Create a missing supported role."""
    return await save_role(AdminRepository(db), user, body)


@router.put('/roles/{role_id}')
async def update_role(role_id: str, body: RoleInput, db: Db, user: User) -> dict:
    """Update a role's description and permissions."""
    return await save_role(AdminRepository(db), user, body, role_id)
