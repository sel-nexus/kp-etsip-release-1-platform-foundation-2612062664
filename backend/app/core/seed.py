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
    portfolio_ids = []
    for name, owner, area in [("Payments", "Payments Team", "Payments"), ("Customer Platform", "Customer Team", "Customer"), ("Data & Analytics", "Data Team", "Data")]:
        await db.portfolios.update_one({"name": name}, {"$setOnInsert": {"name": name, "owner": owner, "description": f"{name} portfolio", "productArea": area, "status": "Active", "createdAt": now, "updatedAt": now}}, upsert=True)
        portfolio = await db.portfolios.find_one({"name": name})
        portfolio_ids.append(str(portfolio["_id"]))
    for index, name in enumerate(["Payment Gateway", "Billing", "Settlement", "Customer Profile", "Member Portal", "Contact Center", "Data Warehouse", "Analytics", "Reporting", "Data Pipeline"]):
        portfolio_index = 0 if index < 3 else 1 if index < 6 else 2
        area = ["Payments", "Customer", "Data"][portfolio_index]
        app = {"name": name, "description": f"{name} application", "portfolioId": portfolio_ids[portfolio_index], "productArea": area, "businessOwner": "Business Team", "technologyOwner": "Technology Team", "etsOwner": "Quality Team", "criticality": "High" if index % 3 == 0 else "Medium", "tier": index % 4 + 1, "lifecycleStatus": "Active", "technologyStack": "Web services", "repositoryLink": None, "pipelineLink": None, "applicationHealthScore": 70 + index, "qualityScore": 75 + index, "automationCoverage": 60 + index, "unitTestCoverage": 65 + index, "openDefects": index, "openCriticalDefects": index % 3, "riskLevel": "High" if index % 3 == 0 else "Low", "createdAt": now, "updatedAt": now}
        await db.applications.update_one({"name": name}, {"$setOnInsert": app}, upsert=True)
    accounts = [("Administrator", "admin"), ("Executive", "executive"), ("Portfolio Leader", "leader"), ("Engineer", "engineer"), ("Auditor", "auditor"), ("Read Only", "readonly")]
    for role, local in accounts:
        email = f"{local}@ketsip.local"
        doc = {"name": role, "email": email, "role": role, "passwordHash": admin_hash if role == "Administrator" else demo_hash, "portfolioAccess": [portfolio_ids[0]] if role in ("Portfolio Leader", "Engineer", "Read Only") else [], "status": "Active", "lastLogin": None, "authSource": "local", "managerName": None, "vendorIndicator": False, "mustChangePassword": role == "Administrator", "defaultLandingPage": "dashboard" if role in ("Administrator", "Executive") else "applications", "tokenVersion": 0, "createdAt": now, "updatedAt": now}
        await db.users.update_one({"email": email}, {"$setOnInsert": doc}, upsert=True)
