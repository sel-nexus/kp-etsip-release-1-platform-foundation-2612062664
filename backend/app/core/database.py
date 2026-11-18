"""Provide the application's pooled MongoDB connection."""

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


def create_client(uri: str) -> AsyncMongoClient:
    """Create a client on the application's event loop."""
    return AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)


def get_db(request: Request) -> AsyncDatabase:
    """Return the database attached to the running application."""
    return request.app.state.db


async def require_transaction_topology(client: AsyncMongoClient) -> None:
    """Reject servers that cannot support sessions and multi-document transactions."""
    hello = await client.admin.command("hello")
    if (not (hello.get("setName") or hello.get("msg") == "isdbgrid")
            or hello.get("logicalSessionTimeoutMinutes") is None
            or hello.get("maxWireVersion", 0) < 8):
        raise RuntimeError("MongoDB must support transactions: use a replica set or mongos with sessions")
