"""Persist and retrieve local accounts in MongoDB."""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase


class UserRepository:
    """Encapsulate account queries and conditional updates."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the users collection."""
        self.db = db
        self.users = db.users

    async def by_email(self, email: str, session: AsyncClientSession | None = None) -> dict | None:
        """Find an account using its normalized email."""
        return await self.users.find_one({"email": email.strip().lower()}, session=session)

    async def by_id(self, user_id: str, session: AsyncClientSession | None = None) -> dict | None:
        """Return an account when its identifier is valid and exists."""
        if not ObjectId.is_valid(user_id):
            return None
        return await self.users.find_one({"_id": ObjectId(user_id)}, session=session)

    async def update(self, user_id: ObjectId, values: dict, increment_version: bool = False) -> None:
        """Update account fields and optionally invalidate earlier tokens."""
        update = {"$set": {**values, "updatedAt": datetime.now(timezone.utc)}}
        if increment_version:
            update["$inc"] = {"tokenVersion": 1}
        await self.users.update_one({"_id": user_id}, update)

    async def update_if_current(self, user: dict, values: dict, session: AsyncClientSession,
                                increment_version: bool = False, require_unrestricted: bool = False) -> dict | None:
        """Write only for the same active identity and session, locking against concurrent changes."""
        query = {"_id": user["_id"], "status": "Active", "role": user["role"],
                 "email": user["email"], "passwordHash": user["passwordHash"]}
        query["tokenVersion"] = user["tokenVersion"] if "tokenVersion" in user else {"$exists": False}
        if require_unrestricted:
            query["mustChangePassword"] = {"$ne": True}
        update = {"$set": {**values, "updatedAt": datetime.now(timezone.utc)}}
        if increment_version:
            update["$inc"] = {"tokenVersion": 1}
        return await self.users.find_one_and_update(query, update, session=session, return_document=ReturnDocument.AFTER)
