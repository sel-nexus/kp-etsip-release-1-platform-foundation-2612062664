"""Create initial roles and local demonstration accounts idempotently."""

from datetime import datetime, timezone
from starlette.concurrency import run_in_threadpool
from pymongo.asynchronous.database import AsyncDatabase
from app.core.config import Settings
from app.core.security import hash_password

ROLES = {
    "Administrator": ["view", "create", "edit", "delete", "export", "administer"],
    "Executive": ["view", "export"],
    "Portfolio Leader": ["view", "create", "edit", "export"],
    "Engineer": ["view", "create", "edit", "export"],
    "Auditor": ["view", "export"],
    "Read Only": ["view"],
}


async def seed_database(db: AsyncDatabase, settings: Settings) -> None:
    """Insert missing roles and users without replacing edited records."""
    now = datetime.now(timezone.utc)
    for role, permissions in ROLES.items():
        await db.roles.update_one({"name": role}, {"$setOnInsert": {"name": role, "description": role, "permissions": permissions, "createdAt": now, "updatedAt": now}}, upsert=True)
    admin_hash = await run_in_threadpool(hash_password, settings.seed_admin_password)
    demo_hash = await run_in_threadpool(hash_password, settings.seed_demo_password)
    accounts = [("Administrator", "admin"), ("Executive", "executive"), ("Portfolio Leader", "leader"), ("Engineer", "engineer"), ("Auditor", "auditor"), ("Read Only", "readonly")]
    for role, local in accounts:
        email = f"{local}@ketsip.local"
        doc = {"name": role, "email": email, "role": role, "passwordHash": admin_hash if role == "Administrator" else demo_hash, "portfolioAccess": [], "status": "Active", "lastLogin": None, "authSource": "local", "managerName": None, "vendorIndicator": False, "mustChangePassword": role == "Administrator", "defaultLandingPage": "dashboard" if role in ("Administrator", "Executive") else "applications", "tokenVersion": 0, "createdAt": now, "updatedAt": now}
        await db.users.update_one({"email": email}, {"$setOnInsert": doc}, upsert=True)
