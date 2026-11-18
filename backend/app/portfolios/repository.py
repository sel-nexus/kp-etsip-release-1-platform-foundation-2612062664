"""Persist portfolios and query their linked application aggregates."""

import re
from datetime import datetime, timezone
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.client_session import AsyncClientSession


class PortfolioRepository:
    """Query the shared MongoDB collections."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the database used by the application."""
        self.db = db

    async def permissions(self, role: str) -> list[str]:
        """Read current editable role permissions."""
        document = await self.db.roles.find_one({"name": role})
        return document.get("permissions", []) if document else []

    async def list(self, scope: list[ObjectId] | None, search: str, status: str | None, product_area: str, limit: int, offset: int) -> dict:
        """Filter and aggregate portfolio records inside MongoDB."""
        match: dict = {}
        if scope is not None:
            match["_id"] = {"$in": scope}
        if search:
            match["name"] = {"$regex": re.escape(search), "$options": "i"}
        if status:
            match["status"] = status
        if product_area:
            match["productArea"] = product_area
        pipeline = [
            {"$match": match},
            {"$lookup": {"from": "applications", "let": {"pid": {"$toString": "$_id"}}, "pipeline": [
                {"$match": {"$expr": {"$eq": ["$portfolioId", "$$pid"]}}},
                {"$group": {"_id": None, "count": {"$sum": 1}, "average": {"$avg": "$qualityScore"}}}
            ], "as": "stats"}},
            {"$set": {"stats": {"$arrayElemAt": ["$stats", 0]}}},
        ]
        pipeline.extend([{"$sort": {"name": 1, "_id": 1}}, {"$facet": {"items": [{"$skip": offset}, {"$limit": limit}], "count": [{"$count": "total"}]}}])
        result = (await (await self.db.portfolios.aggregate(pipeline)).to_list(1))[0]
        return {"items": result["items"], "total": result["count"][0]["total"] if result["count"] else 0}

    async def get(self, ident: ObjectId, session: AsyncClientSession | None = None) -> dict | None:
        """Find a portfolio and compute its application statistics."""
        pipeline = [{"$match": {"_id": ident}}, {"$lookup": {"from": "applications", "let": {"pid": {"$toString": "$_id"}}, "pipeline": [{"$match": {"$expr": {"$eq": ["$portfolioId", "$$pid"]}}}, {"$group": {"_id": None, "count": {"$sum": 1}, "average": {"$avg": "$qualityScore"}}}], "as": "stats"}}, {"$set": {"stats": {"$arrayElemAt": ["$stats", 0]}}}]
        docs = await (await self.db.portfolios.aggregate(pipeline, session=session)).to_list(1)
        return docs[0] if docs else None

    async def applications(self, ident: ObjectId, limit: int, offset: int) -> dict:
        """List a bounded page of applications referencing the portfolio."""
        query = {"portfolioId": str(ident)}
        cursor = self.db.applications.find(query, {"name": 1, "productArea": 1, "qualityScore": 1, "riskLevel": 1}).sort([("name", 1), ("_id", 1)]).skip(offset).limit(limit)
        docs = await cursor.to_list(limit)
        return {"items": [{"id": str(d.pop("_id")), **d} for d in docs], "total": await self.db.applications.count_documents(query)}

    async def create(self, data: dict, session: AsyncClientSession) -> ObjectId:
        """Insert a new portfolio with UTC timestamps."""
        now = datetime.now(timezone.utc)
        return (await self.db.portfolios.insert_one({**data, "createdAt": now, "updatedAt": now}, session=session)).inserted_id

    async def update(self, ident: ObjectId, data: dict, session: AsyncClientSession) -> bool:
        """Update editable fields on an existing portfolio."""
        result = await self.db.portfolios.update_one({"_id": ident}, {"$set": {**data, "updatedAt": datetime.now(timezone.utc)}}, session=session)
        return bool(result.matched_count)

    async def has_applications(self, ident: ObjectId, session: AsyncClientSession) -> bool:
        """Check for references before deletion."""
        return await self.db.applications.find_one({"portfolioId": str(ident)}, {"_id": 1}, session=session) is not None

    async def has_assignments(self, ident: ObjectId, session: AsyncClientSession) -> bool:
        """Check all user assignments, including inactive accounts."""
        return await self.db.users.find_one({"portfolioAccess": str(ident)}, {"_id": 1}, session=session) is not None

    async def delete(self, ident: ObjectId, session: AsyncClientSession) -> bool:
        """Remove an existing portfolio."""
        return bool((await self.db.portfolios.delete_one({"_id": ident}, session=session)).deleted_count)

    async def audit(self, user: dict, action: str, ident: ObjectId, session: AsyncClientSession) -> None:
        """Record a successful portfolio mutation without credentials."""
        now = datetime.now(timezone.utc)
        await self.db.audit_logs.insert_one({"timestamp": now, "createdAt": now, "updatedAt": now, "userId": user["_id"], "userName": user["name"], "action": action, "entityType": "portfolio", "entityId": str(ident), "details": f"Portfolio {action}"}, session=session)
