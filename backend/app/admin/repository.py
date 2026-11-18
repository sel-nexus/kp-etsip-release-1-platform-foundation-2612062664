"""Persist administration changes and query the audit history."""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase


class AdminRepository:
    """Access the shared collections for administration."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the shared database."""
        self.db = db

    async def permissions(self, role: str) -> list[str]:
        """Read the role's current permission set."""
        doc = await self.db.roles.find_one({'name': role})
        return doc.get('permissions', []) if doc else []

    async def get(self, collection: str, ident: ObjectId) -> dict | None:
        """Find a document by identifier."""
        return await self.db[collection].find_one({'_id': ident})

    async def page(self, collection: str, query: dict, limit: int, offset: int) -> dict:
        """Read a bounded page and its total."""
        sort = [('timestamp', -1), ('_id', -1)] if collection == 'audit_logs' else [('name', 1), ('_id', 1)]
        docs = await self.db[collection].find(query).sort(sort).skip(offset).limit(limit).to_list(limit)
        return {'items': docs, 'total': await self.db[collection].count_documents(query)}

    async def insert(self, collection: str, values: dict) -> ObjectId:
        """Insert a timestamped document."""
        now = datetime.now(timezone.utc)
        return (await self.db[collection].insert_one({**values, 'createdAt': now, 'updatedAt': now})).inserted_id

    async def update(self, collection: str, ident: ObjectId, values: dict) -> bool:
        """Persist fields on an existing document."""
        result = await self.db[collection].update_one({'_id': ident}, {'$set': {**values, 'updatedAt': datetime.now(timezone.utc)}})
        return bool(result.matched_count)

    async def valid_portfolios(self, values: list[str]) -> bool:
        """Ensure each assigned portfolio exists."""
        if len(set(values)) != len(values) or any(not ObjectId.is_valid(v) for v in values):
            return False
        return await self.db.portfolios.count_documents({'_id': {'$in': [ObjectId(v) for v in values]}}) == len(values)

    async def active_admin_count(self) -> int:
        """Count active administrator accounts."""
        return await self.db.users.count_documents({'role': 'Administrator', 'status': 'Active'})

    async def audit(self, actor: dict, action: str, kind: str, ident: ObjectId | None, details: str) -> None:
        """Persist a credential-free event for a successful operation."""
        now = datetime.now(timezone.utc)
        await self.db.audit_logs.insert_one({'timestamp': now, 'createdAt': now, 'updatedAt': now, 'userId': actor['_id'], 'userName': actor['name'], 'action': action, 'entityType': kind, 'entityId': str(ident) if ident else None, 'details': details})
