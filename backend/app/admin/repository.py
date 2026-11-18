"""Persist administration changes and query the audit history."""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.client_session import AsyncClientSession


class AdminRepository:
    """Access the shared collections for administration."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the shared database."""
        self.db = db

    async def permissions(self, role: str, session: AsyncClientSession | None = None) -> list[str]:
        """Read the role's current permission set."""
        doc = await self.db.roles.find_one({'name': role}, session=session)
        return doc.get('permissions', []) if doc else []

    async def get(self, collection: str, ident: ObjectId, session: AsyncClientSession | None = None) -> dict | None:
        """Find a document by identifier."""
        return await self.db[collection].find_one({'_id': ident}, session=session)

    async def page(self, collection: str, query: dict, limit: int, offset: int) -> dict:
        """Read a bounded page and its total."""
        sort = [('timestamp', -1), ('_id', -1)] if collection == 'audit_logs' else [('name', 1), ('_id', 1)]
        docs = await self.db[collection].find(query).sort(sort).skip(offset).limit(limit).to_list(limit)
        return {'items': docs, 'total': await self.db[collection].count_documents(query)}

    async def insert(self, collection: str, values: dict, session: AsyncClientSession | None = None) -> ObjectId:
        """Insert a timestamped document."""
        now = datetime.now(timezone.utc)
        return (await self.db[collection].insert_one({**values, 'createdAt': now, 'updatedAt': now}, session=session)).inserted_id

    async def update(self, collection: str, ident: ObjectId, values: dict, session: AsyncClientSession | None = None) -> bool:
        """Persist fields on an existing document."""
        result = await self.db[collection].update_one({'_id': ident}, {'$set': {**values, 'updatedAt': datetime.now(timezone.utc)}}, session=session)
        return bool(result.matched_count)

    async def valid_portfolios(self, values: list[str]) -> bool:
        """Ensure each assigned portfolio exists."""
        if len(set(values)) != len(values) or any(not ObjectId.is_valid(v) for v in values):
            return False
        return await self.db.portfolios.count_documents({'_id': {'$in': [ObjectId(v) for v in values]}}) == len(values)

    async def active_admin_count(self, session: AsyncClientSession | None = None) -> int:
        """Count active administrator accounts."""
        return await self.db.users.count_documents({'role': 'Administrator', 'status': 'Active'}, session=session)

    async def audit(self, actor: dict, action: str, kind: str, ident: ObjectId | None, details: str, session: AsyncClientSession | None = None) -> None:
        """Persist a credential-free event for a successful operation."""
        now = datetime.now(timezone.utc)
        await self.db.audit_logs.insert_one({'timestamp': now, 'createdAt': now, 'updatedAt': now, 'userId': actor['_id'], 'userName': actor['name'], 'action': action, 'entityType': kind, 'entityId': str(ident) if ident else None, 'details': details}, session=session)

    async def guard_admin(self, actor: dict, session: AsyncClientSession) -> dict | None:
        """Serialize admin changes and lock the actor against concurrent revocation."""
        role = await self.db.roles.find_one_and_update(
            {'name': 'Administrator'}, {'$inc': {'_adminVersion': 1}}, session=session
        )
        if not role or 'administer' not in role.get('permissions', []):
            return None
        query = {'_id': actor['_id'], 'role': 'Administrator', 'status': 'Active'}
        query['tokenVersion'] = actor['tokenVersion'] if 'tokenVersion' in actor else {'$exists': False}
        return await self.db.users.find_one_and_update(
            query, {'$inc': {'_adminAuthVersion': 1}}, session=session
        )
