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
