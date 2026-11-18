"""Apply authentication and profile rules."""

from datetime import datetime, timezone
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from app.audit.repository import AuditRepository
from app.audit.service import record_event
from app.auth.repository import UserRepository
from app.core.security import create_token, hash_password, verify_password
from app.core.transactions import transact


def public_user(user: dict) -> dict:
    """Return only the explicitly permitted profile fields."""
    return {"id": str(user["_id"]), "name": user["name"], "email": user["email"], "role": user["role"], "portfolioAccess": user.get("portfolioAccess", []), "status": user["status"], "defaultLandingPage": user.get("defaultLandingPage", "dashboard" if user["role"] in ("Administrator", "Executive") else "applications"), "mustChangePassword": user.get("mustChangePassword", False)}


def invalid_session() -> HTTPException:
    """Reject an identity that changed since authentication."""
    return HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})


async def login(users: UserRepository, audit: AuditRepository, email: str, password: str, secret: str) -> dict:
    """Commit the login event and last-login update together before issuing a token."""
    user = await users.by_email(email)
    valid = user is not None and await run_in_threadpool(verify_password, password, user["passwordHash"])

    async def operation(session):
        """Recheck the verified credential when acquiring the account write lock."""
        live = await users.by_email(email, session)
        saved = None
        if valid and live and user and live["_id"] == user["_id"] and live.get("passwordHash") == user["passwordHash"]:
            saved = await users.update_if_current(live, {"lastLogin": datetime.now(timezone.utc)}, session)
        await record_event(audit, saved or live, email.strip().lower(), "login", "Login succeeded" if saved else "Login failed", session)
        return saved

    saved = await transact(users.db, operation)
    if not saved:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"token": create_token(saved, secret), "user": public_user(saved)}


async def change_password(users: UserRepository, audit: AuditRepository, user: dict, current: str, new: str, secret: str) -> dict:
    """Verify credentials and atomically rotate the password, session, and audit."""
    if not await run_in_threadpool(verify_password, current, user["passwordHash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if await run_in_threadpool(verify_password, new, user["passwordHash"]):
        raise HTTPException(status_code=400, detail="Choose a different password")
    hashed = await run_in_threadpool(hash_password, new)

    async def operation(session):
        """Reject a concurrently changed identity rather than overwriting it."""
        saved = await users.update_if_current(user, {"passwordHash": hashed, "mustChangePassword": False}, session, increment_version=True)
        if not saved:
            raise invalid_session()
        await record_event(audit, saved, saved["email"], "edit", "Password changed", session)
        return saved

    saved = await transact(users.db, operation)
    return {"token": create_token(saved, secret), "user": public_user(saved)}


async def change_landing(users: UserRepository, audit: AuditRepository, user: dict, landing: str) -> dict:
    """Persist a preference and its audit record in one transaction."""
    async def operation(session):
        """Revalidate the session and initial-password restriction while writing."""
        saved = await users.update_if_current(user, {"defaultLandingPage": landing}, session, require_unrestricted=True)
        if not saved:
            raise invalid_session()
        await record_event(audit, saved, saved["email"], "edit", "Landing preference changed", session)
        return saved
    return public_user(await transact(users.db, operation))


async def logout(users: UserRepository, audit: AuditRepository, user: dict) -> None:
    """Invalidate the current session and commit its audit event together."""
    async def operation(session):
        """Revalidate the session while invalidating it."""
        saved = await users.update_if_current(user, {}, session, increment_version=True)
        if not saved:
            raise invalid_session()
        await record_event(audit, saved, saved["email"], "edit", "Signed out", session)
    await transact(users.db, operation)
