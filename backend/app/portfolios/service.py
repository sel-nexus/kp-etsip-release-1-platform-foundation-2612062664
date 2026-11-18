"""Enforce portfolio permissions and shape computed responses."""

import math
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.portfolios.repository import PortfolioRepository
from app.portfolios.schemas import PortfolioInput
from app.core.transactions import guard_inventory_actor, guard_portfolio, transact

SCOPED = {"Portfolio Leader", "Engineer", "Read Only"}


def identifier(value: str) -> ObjectId:
    """Reject invalid identifiers without leaking driver exceptions."""
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return ObjectId(value)


def scope(user: dict) -> list[ObjectId] | None:
    """Return assigned identifiers for roles with restricted visibility."""
    if user["role"] not in SCOPED:
        return None
    return [ObjectId(value) for value in user.get("portfolioAccess", []) if isinstance(value, str) and ObjectId.is_valid(value)]


async def authorize(repo: PortfolioRepository, user: dict, permission: str, ident: ObjectId | None = None, permissions: list[str] | None = None) -> list[str]:
    """Intersect current generic permissions with fixed role and scope rules."""
    if permissions is None:
        permissions = await repo.permissions(user["role"])
    role = user["role"]
    allowed_role = {"view": {"Administrator", "Executive", "Portfolio Leader", "Engineer", "Auditor", "Read Only"}, "create": {"Administrator"}, "edit": {"Administrator", "Portfolio Leader"}, "delete": {"Administrator"}}
    if permission not in permissions or role not in allowed_role[permission]:
        raise HTTPException(status_code=403, detail="Not permitted")
    visible = scope(user)
    if ident is not None and visible is not None and ident not in visible:
        raise HTTPException(status_code=403, detail="Portfolio outside assigned scope")
    return permissions


def present(doc: dict, user: dict, permissions: list[str]) -> dict:
    """Expose live counts and round the average to the nearest integer."""
    stats = doc.get("stats") or {}
    average = stats.get("average")
    visible = scope(user)
    can_edit = "edit" in permissions and (user["role"] == "Administrator" or (user["role"] == "Portfolio Leader" and visible is not None and doc["_id"] in visible))
    return {"id": str(doc["_id"]), "name": doc["name"], "owner": doc.get("owner", ""), "description": doc.get("description", ""), "productArea": doc.get("productArea", ""), "status": doc.get("status", "Active"), "applicationCount": stats.get("count", 0), "qualityScore": math.floor(average + 0.5) if average is not None else None, "canEdit": can_edit, "canDelete": user["role"] == "Administrator" and "delete" in permissions}


async def list_portfolios(repo: PortfolioRepository, user: dict, search: str, status: str | None, product_area: str, limit: int, offset: int) -> dict:
    """List only visible portfolios and current action capabilities."""
    permissions = await authorize(repo, user, "view")
    page = await repo.list(scope(user), search, status, product_area, limit, offset)
    return {"items": [present(doc, user, permissions) for doc in page["items"]], "total": page["total"], "canCreate": user["role"] == "Administrator" and "create" in permissions}


async def detail(repo: PortfolioRepository, user: dict, value: str) -> dict:
    """Read a visible portfolio with computed statistics."""
    ident = identifier(value)
    permissions = await authorize(repo, user, "view", ident)
    doc = await repo.get(ident)
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return present(doc, user, permissions)


async def mutate(repo: PortfolioRepository, user: dict, action: str, value: str | None = None, body: PortfolioInput | None = None) -> dict | None:
    """Validate authorization, persist a mutation, and record its audit event."""
    ident = identifier(value) if value else None

    async def operation(session):
        """Apply one retryable mutation and its audit event."""
        guarded = await guard_inventory_actor(repo.db, user, session)
        if not guarded:
            raise HTTPException(status_code=403, detail="Not permitted")
        live_user, permissions = guarded
        await authorize(repo, live_user, action, ident, permissions)
        key = ident
        if action == "create" and body is not None:
            key = await repo.create(body.model_dump(), session)
        elif action == "edit" and key is not None and body is not None:
            if not await repo.update(key, body.model_dump(), session):
                raise HTTPException(status_code=404, detail="Portfolio not found")
        elif action == "delete" and key is not None:
            if not await guard_portfolio(repo.db, key, session):
                raise HTTPException(status_code=404, detail="Portfolio not found")
            if await repo.has_applications(key, session):
                raise HTTPException(status_code=409, detail="Portfolio contains applications")
            if await repo.has_assignments(key, session):
                raise HTTPException(status_code=409, detail="Portfolio has user assignments")
            if not await repo.delete(key, session):
                raise HTTPException(status_code=404, detail="Portfolio not found")
        else:
            raise HTTPException(status_code=422, detail="Invalid portfolio mutation")
        await repo.audit(live_user, action, key, session)
        if action == "delete":
            return None
        return present(await repo.get(key, session), live_user, permissions)

    try:
        return await transact(repo.db, operation)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Portfolio name already exists") from exc
