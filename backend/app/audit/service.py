"""Construct sensitive activity audit events."""

from datetime import datetime, timezone
from app.audit.repository import AuditRepository
from pymongo.asynchronous.client_session import AsyncClientSession


async def record_event(repository: AuditRepository, user: dict | None, submitted_email: str, action: str, details: str, session: AsyncClientSession | None = None) -> None:
    """Record an event without storing credentials or tokens."""
    now = datetime.now(timezone.utc)
    await repository.insert({"timestamp": now, "createdAt": now, "updatedAt": now, "userId": user["_id"] if user else None, "userName": user["name"] if user else submitted_email, "action": action, "entityType": "user", "entityId": str(user["_id"]) if user else None, "details": details}, session=session)
