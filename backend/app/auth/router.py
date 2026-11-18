"""Expose session and profile endpoints."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pymongo.asynchronous.database import AsyncDatabase
from app.audit.repository import AuditRepository
from app.auth.repository import UserRepository
from app.auth.schemas import LandingChange, LoginRequest, LoginResponse, PasswordChange, PublicUser
from app.auth.service import change_landing, change_password, login, logout as logout_service, public_user
from app.core.database import get_db
from app.core.security import decode_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)


async def current_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: Annotated[AsyncDatabase, Depends(get_db)]) -> dict:
    """Resolve every token against the current active database account."""
    unauthorized = HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise unauthorized
    try:
        claims = decode_token(credentials.credentials, request.app.state.settings.jwt_secret)
        user = await UserRepository(db).by_id(claims["sub"])
    except (JWTError, KeyError, TypeError):
        raise unauthorized
    if not user or user["status"] != "Active" or claims.get("ver") != user.get("tokenVersion", 0):
        raise unauthorized
    return user


async def unrestricted_user(user: Annotated[dict, Depends(current_user)]) -> dict:
    """Enforce initial password change before preference or future protected work."""
    if user.get("mustChangePassword", False):
        raise HTTPException(status_code=403, detail="Password change required")
    return user


@router.post("/login", response_model=LoginResponse)
async def login_route(body: LoginRequest, request: Request, db: Annotated[AsyncDatabase, Depends(get_db)]) -> dict:
    """Authenticate a local account and issue its signed token."""
    return await login(UserRepository(db), AuditRepository(db), body.email, body.password, request.app.state.settings.jwt_secret)


@router.get("/me", response_model=PublicUser)
async def me(user: Annotated[dict, Depends(current_user)]) -> dict:
    """Return the current public profile, including initial-change status."""
    return public_user(user)


@router.post("/password", response_model=LoginResponse)
async def password(body: PasswordChange, request: Request, db: Annotated[AsyncDatabase, Depends(get_db)], user: Annotated[dict, Depends(current_user)]) -> dict:
    """Change the current user's password and replace the session."""
    return await change_password(UserRepository(db), AuditRepository(db), user, body.currentPassword, body.newPassword, request.app.state.settings.jwt_secret)


@router.put("/landing", response_model=PublicUser)
async def landing(body: LandingChange, db: Annotated[AsyncDatabase, Depends(get_db)], user: Annotated[dict, Depends(unrestricted_user)]) -> dict:
    """Save the current user's preferred landing destination."""
    return await change_landing(UserRepository(db), AuditRepository(db), user, body.defaultLandingPage)


@router.post("/logout", status_code=204)
async def logout(db: Annotated[AsyncDatabase, Depends(get_db)], user: Annotated[dict, Depends(current_user)]) -> None:
    """Invalidate the user's tokens and record sign-out."""
    await logout_service(UserRepository(db), AuditRepository(db), user)
