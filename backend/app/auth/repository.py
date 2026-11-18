"""Persist and retrieve local accounts in MongoDB."""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase


class UserRepository:
    """Encapsulate account queries and atomic updates."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the users collection."""
        self.users = db.users

    async def by_email(self, email: str) -> dict | None:
        """Find an account using its normalized email."""
        return await self.users.find_one({"email": email.strip().lower()})

    async def by_id(self, user_id: str) -> dict | None:
        """Return an account when its identifier is valid and exists."""
        if not ObjectId.is_valid(user_id):
            return None
        return await self.users.find_one({"_id": ObjectId(user_id)})

    async def update(self, user_id: ObjectId, values: dict, increment_version: bool = False) -> None:
        """Update account fields and optionally invalidate earlier tokens."""
        update = {"$set": {**values, "updatedAt": datetime.now(timezone.utc)}}
        if increment_version:
            update["$inc"] = {"tokenVersion": 1}
        await self.users.update_one({"_id": user_id}, update)
