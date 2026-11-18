"""Enforce application permissions, scope and persistence rules."""

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.applications.repository import ApplicationRepository
from app.applications.schemas import ApplicationInput

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


async def authorize(repo: ApplicationRepository, user: dict, action: str, portfolio_id: str | None = None) -> list[str]:
    """Intersect live grants with fixed role and portfolio restrictions."""
    permissions = await repo.permissions(user["role"])
    roles = {"view": {"Administrator", "Executive", "Portfolio Leader", "Engineer", "Auditor", "Read Only"}, "create": MUTATORS, "edit": MUTATORS, "delete": {"Administrator"}}
    if action not in permissions or user["role"] not in roles[action]:
        raise HTTPException(status_code=403, detail="Not permitted")
    visible = scope(user)
    if portfolio_id is not None and visible is not None and portfolio_id not in visible:
        raise HTTPException(status_code=403, detail="Portfolio outside assigned scope")
    return permissions


async def present(repo: ApplicationRepository, doc: dict, user: dict, permissions: list[str]) -> dict:
    """Add a portfolio label and live effective action flags."""
    portfolio = await repo.portfolio(doc["portfolioId"])
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
    await authorize(repo, user, action)
    ident = identifier(value) if value else None
    existing = await repo.get(ident) if ident else None
    if ident and not existing:
        raise HTTPException(status_code=404, detail="Application not found")
    if existing:
        await authorize(repo, user, action, existing["portfolioId"])
    if body is not None:
        await authorize(repo, user, action, body.portfolioId)
        if not await repo.portfolio(body.portfolioId):
            raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        if action == "create" and body:
            ident = await repo.create(body.model_dump(mode="json"))
        elif action == "edit" and body and ident and existing:
            if not await repo.update(ident, existing["portfolioId"], body.model_dump(mode="json")):
                raise HTTPException(status_code=409, detail="Application changed; reload and retry")
        elif action == "delete" and ident:
            if not await repo.delete(ident):
                raise HTTPException(status_code=404, detail="Application not found")
        else:
            raise HTTPException(status_code=422, detail="Invalid application mutation")
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Application name already exists") from exc
    await repo.audit(user, action, ident)
    if action == "delete":
        return None
    return await present(repo, await repo.get(ident), user, await repo.permissions(user["role"]))
