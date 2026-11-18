"""Persist audit events in MongoDB."""

from pymongo.asynchronous.database import AsyncDatabase


class AuditRepository:
    """Write events to the audit collection."""
    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the collection for the current application."""
        self.logs = db.audit_logs

    async def insert(self, event: dict) -> None:
        """Persist one audit event before acknowledging its operation."""
        await self.logs.insert_one(event)
