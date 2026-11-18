"""Persist and retrieve application inventory in MongoDB."""

import re
from datetime import datetime, timezone
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase


class ApplicationRepository:
    """Use the shared async database for applications and their references."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the shared database."""
        self.db = db

    async def permissions(self, role: str) -> list[str]:
        """Read the current permissions for a role."""
        role_doc = await self.db.roles.find_one({"name": role})
        return role_doc.get("permissions", []) if role_doc else []

    async def portfolio(self, ident: str) -> dict | None:
        """Find the referenced portfolio."""
        return await self.db.portfolios.find_one({"_id": ObjectId(ident)})

    async def get(self, ident: ObjectId) -> dict | None:
        """Find an application by its Mongo identifier."""
        return await self.db.applications.find_one({"_id": ident})

    async def list(self, scope: list[str] | None, search: str, portfolio_id: str | None, criticality: str | None, risk: str | None, lifecycle: str | None, limit: int, offset: int) -> dict:
        """Apply safe literal filters and return a bounded page."""
        query: dict = {}
        if scope is not None:
            query["portfolioId"] = {"$in": scope}
        if portfolio_id:
            if scope is not None and portfolio_id not in scope:
                query["portfolioId"] = {"$in": []}
            else:
                query["portfolioId"] = portfolio_id
        if search:
            query["name"] = {"$regex": re.escape(search), "$options": "i"}
        for key, value in (("criticality", criticality), ("riskLevel", risk), ("lifecycleStatus", lifecycle)):
            if value:
                query[key] = value
        cursor = self.db.applications.find(query).sort([("name", 1), ("_id", 1)]).skip(offset).limit(limit)
        return {"items": await cursor.to_list(limit), "total": await self.db.applications.count_documents(query)}

    async def create(self, data: dict) -> ObjectId:
        """Insert the validated record with timestamps."""
        now = datetime.now(timezone.utc)
        return (await self.db.applications.insert_one({**data, "createdAt": now, "updatedAt": now})).inserted_id

    async def update(self, ident: ObjectId, old_portfolio: str, data: dict) -> bool:
        """Replace editable fields only if the original scope is unchanged."""
        result = await self.db.applications.update_one({"_id": ident, "portfolioId": old_portfolio}, {"$set": {**data, "updatedAt": datetime.now(timezone.utc)}})
        return bool(result.matched_count)

    async def delete(self, ident: ObjectId) -> bool:
        """Remove the selected record."""
        return bool((await self.db.applications.delete_one({"_id": ident})).deleted_count)

    async def audit(self, user: dict, action: str, ident: ObjectId) -> None:
        """Record a successful mutation without sensitive data."""
        now = datetime.now(timezone.utc)
        await self.db.audit_logs.insert_one({"timestamp": now, "createdAt": now, "updatedAt": now, "userId": user["_id"], "userName": user["name"], "action": action, "entityType": "application", "entityId": str(ident), "details": f"Application {action}"})
