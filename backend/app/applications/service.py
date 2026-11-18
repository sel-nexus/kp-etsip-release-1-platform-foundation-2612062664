"""Enforce application permissions, scope and persistence rules."""

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.applications.repository import ApplicationRepository
from app.applications.schemas import ApplicationInput
from app.core.transactions import guard_inventory_actor, guard_portfolio, transact

SCOPED = {"Portfolio Leader", "Engineer", "Read Only"}
MUTATORS = {"Administrator", "Portfolio Leader", "Engineer"}


def scope(user: dict) -> list[str] | None:
    """Return the assigned string identifiers, or unrestricted scope."""
    if user["role"] not in SCOPED:
        return None
    return [str(ObjectId(value)) for value in user.get("portfolioAccess", []) if isinstance(value, str) and ObjectId.is_valid(value)]


def identifier(value: str) -> ObjectId:
    """Reject invalid application identifiers."""
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail="Application not found")
    return ObjectId(value)


async def authorize(repo: ApplicationRepository, user: dict, action: str, portfolio_id: str | None = None, permissions: list[str] | None = None) -> list[str]:
    """Intersect live grants with fixed role and portfolio restrictions."""
    if permissions is None:
        permissions = await repo.permissions(user["role"])
    roles = {"view": {"Administrator", "Executive", "Portfolio Leader", "Engineer", "Auditor", "Read Only"}, "create": MUTATORS, "edit": MUTATORS, "delete": {"Administrator"}}
    if action not in permissions or user["role"] not in roles[action]:
        raise HTTPException(status_code=403, detail="Not permitted")
    visible = scope(user)
    if portfolio_id is not None and visible is not None and portfolio_id not in visible:
        raise HTTPException(status_code=403, detail="Portfolio outside assigned scope")
    return permissions


async def present(repo: ApplicationRepository, doc: dict, user: dict, permissions: list[str], session=None) -> dict:
    """Add a portfolio label and live effective action flags."""
    portfolio = await repo.portfolio(doc["portfolioId"], session)
    fields = {key: doc[key] for key in ApplicationInput.model_fields if key in doc}
    return {**fields, "id": str(doc["_id"]), "portfolioName": portfolio["name"] if portfolio else "Unknown portfolio", "canEdit": user["role"] in MUTATORS and "edit" in permissions, "canDelete": user["role"] == "Administrator" and "delete" in permissions}


async def listing(repo: ApplicationRepository, user: dict, search: str, portfolio_id: str | None, criticality: str | None, risk: str | None, lifecycle: str | None, limit: int, offset: int) -> dict:
    """List only records within the caller's assigned scope."""
    permissions = await authorize(repo, user, "view")
    page = await repo.list(scope(user), search, portfolio_id, criticality, risk, lifecycle, limit, offset)
    return {"items": [await present(repo, doc, user, permissions) for doc in page["items"]], "total": page["total"], "canCreate": user["role"] in MUTATORS and "create" in permissions}


async def detail(repo: ApplicationRepository, user: dict, value: str) -> dict:
    """Read a visible application or reject access."""
    ident = identifier(value)
    permissions = await authorize(repo, user, "view")
    doc = await repo.get(ident)
    if not doc:
        raise HTTPException(status_code=404, detail="Application not found")
    await authorize(repo, user, "view", doc["portfolioId"])
    return await present(repo, doc, user, permissions)


async def mutate(repo: ApplicationRepository, user: dict, action: str, value: str | None = None, body: ApplicationInput | None = None) -> dict | None:
    """Check source and destination scope, save, and audit the mutation."""
    ident = identifier(value) if value else None

    async def operation(session):
        """Re-read scope and apply one retryable mutation with its audit."""
        guarded = await guard_inventory_actor(repo.db, user, session)
        if not guarded:
            raise HTTPException(status_code=403, detail="Not permitted")
        live_user, permissions = guarded
        await authorize(repo, live_user, action, permissions=permissions)
        key = ident
        existing = await repo.get(key, session) if key else None
        if key and not existing:
            raise HTTPException(status_code=404, detail="Application not found")
        if existing:
            await authorize(repo, live_user, action, existing["portfolioId"], permissions)
        if body is not None:
            await authorize(repo, live_user, action, body.portfolioId, permissions)
            if not await guard_portfolio(repo.db, ObjectId(body.portfolioId), session):
                raise HTTPException(status_code=404, detail="Portfolio not found")
        if action == "create" and body:
            key = await repo.create(body.model_dump(mode="json"), session)
        elif action == "edit" and body and key and existing:
            if not await repo.update(key, existing["portfolioId"], body.model_dump(mode="json"), session):
                raise HTTPException(status_code=409, detail="Application changed; reload and retry")
        elif action == "delete" and key:
            if not await repo.delete(key, session):
                raise HTTPException(status_code=404, detail="Application not found")
        else:
            raise HTTPException(status_code=422, detail="Invalid application mutation")
        await repo.audit(live_user, action, key, session)
        if action == "delete":
            return None
        return await present(repo, await repo.get(key, session), live_user, permissions, session)

    try:
        return await transact(repo.db, operation)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Application name already exists") from exc
