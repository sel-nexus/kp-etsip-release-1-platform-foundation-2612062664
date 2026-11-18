"""Apply authentication and profile rules."""

from datetime import datetime, timezone
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from app.audit.repository import AuditRepository
from app.audit.service import record_event
from app.auth.repository import UserRepository
from app.core.security import create_token, hash_password, verify_password


def public_user(user: dict) -> dict:
    """Return only the explicitly permitted profile fields."""
    return {"id": str(user["_id"]), "name": user["name"], "email": user["email"], "role": user["role"], "portfolioAccess": user.get("portfolioAccess", []), "status": user["status"], "defaultLandingPage": user.get("defaultLandingPage", "dashboard" if user["role"] in ("Administrator", "Executive") else "applications"), "mustChangePassword": user.get("mustChangePassword", False)}


async def login(users: UserRepository, audit: AuditRepository, email: str, password: str, secret: str) -> dict:
    """Authenticate and audit both successful and failed attempts."""
    user = await users.by_email(email)
    valid = user is not None and await run_in_threadpool(verify_password, password, user["passwordHash"])
    if not valid or user["status"] != "Active":
        await record_event(audit, user, email.strip().lower(), "login", "Login failed")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await record_event(audit, user, email, "login", "Login succeeded")
    await users.update(user["_id"], {"lastLogin": datetime.now(timezone.utc)})
    return {"token": create_token(user, secret), "user": public_user(user)}


async def change_password(users: UserRepository, audit: AuditRepository, user: dict, current: str, new: str, secret: str) -> dict:
    """Verify the old password, save a replacement, and rotate the session."""
    if not await run_in_threadpool(verify_password, current, user["passwordHash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if await run_in_threadpool(verify_password, new, user["passwordHash"]):
        raise HTTPException(status_code=400, detail="Choose a different password")
    hashed = await run_in_threadpool(hash_password, new)
    await users.update(user["_id"], {"passwordHash": hashed, "mustChangePassword": False}, increment_version=True)
    await record_event(audit, user, user["email"], "edit", "Password changed")
    refreshed = await users.by_id(str(user["_id"]))
    return {"token": create_token(refreshed, secret), "user": public_user(refreshed)}


async def change_landing(users: UserRepository, audit: AuditRepository, user: dict, landing: str) -> dict:
    """Persist the selected landing page and record the change."""
    await users.update(user["_id"], {"defaultLandingPage": landing})
    await record_event(audit, user, user["email"], "edit", "Landing preference changed")
    return public_user(await users.by_id(str(user["_id"])))
