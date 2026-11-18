"""Provide a shared transaction and portfolio-reference guard."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

T = TypeVar("T")


async def transact(db: AsyncDatabase, callback: Callable[[AsyncClientSession], Awaitable[T]]) -> T:
    """Run and retry a transaction; never fall back to non-transactional writes."""
    async with db.client.start_session() as session:
        return await session.with_transaction(
            callback, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
        )


async def guard_portfolio(db: AsyncDatabase, ident: ObjectId, session: AsyncClientSession) -> bool:
    """Write the shared guard so reference writers conflict with deletion.

    User-assignment writers must use this same guard in their transaction before
    validating and persisting assignments; otherwise deletion can race them.
    """
    result = await db.portfolios.update_one(
        {"_id": ident}, {"$inc": {"_referenceVersion": 1}}, session=session
    )
    return bool(result.matched_count)


async def guard_inventory_actor(db: AsyncDatabase, actor: dict, session: AsyncClientSession) -> tuple[dict, list[str]] | None:
    """Lock the role and active session in admin's order and return live authorization state."""
    role = await db.roles.find_one_and_update(
        {"name": actor["role"]}, {"$inc": {"_adminVersion": 1}}, session=session
    )
    if not role:
        return None
    query = {"_id": actor["_id"], "role": actor["role"], "status": "Active",
             "mustChangePassword": {"$ne": True}}
    query["tokenVersion"] = actor["tokenVersion"] if "tokenVersion" in actor else {"$exists": False}
    live = await db.users.find_one_and_update(
        query, {"$inc": {"_adminAuthVersion": 1}}, session=session
    )
    if not live:
        return None
    return live, role.get("permissions", [])
