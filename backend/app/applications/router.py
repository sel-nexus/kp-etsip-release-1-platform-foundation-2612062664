"""Expose scoped application inventory endpoints."""

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, Response
from pymongo.asynchronous.database import AsyncDatabase
from app.auth.router import unrestricted_user
from app.core.database import get_db
from app.applications.repository import ApplicationRepository
from app.applications.schemas import Application, ApplicationInput, ApplicationPage
from app.applications.service import detail, listing, mutate

router = APIRouter(prefix="/api/applications", tags=["Applications"])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


@router.get("", response_model=ApplicationPage)
async def list_applications(db: Db, user: User, search: str = Query("", max_length=200), portfolioId: str | None = Query(None, min_length=24, max_length=24, pattern=r"^[0-9a-fA-F]{24}$"), criticality: Literal["Low", "Medium", "High", "Critical"] | None = None, riskLevel: Literal["Low", "Medium", "High"] | None = None, lifecycleStatus: Literal["Active", "Deprecated", "Retired"] | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List visible applications with bounded filters."""
    return await listing(ApplicationRepository(db), user, search, portfolioId.lower() if portfolioId else None, criticality, riskLevel, lifecycleStatus, limit, offset)


@router.post("", response_model=Application, status_code=201)
async def create(body: ApplicationInput, db: Db, user: User) -> dict:
    """Create an application in an authorized portfolio."""
    return await mutate(ApplicationRepository(db), user, "create", body=body)


@router.get("/{application_id}", response_model=Application)
async def get(application_id: str, db: Db, user: User) -> dict:
    """Read a visible application."""
    return await detail(ApplicationRepository(db), user, application_id)


@router.put("/{application_id}", response_model=Application)
async def update(application_id: str, body: ApplicationInput, db: Db, user: User) -> dict:
    """Update an application in authorized portfolios."""
    return await mutate(ApplicationRepository(db), user, "edit", application_id, body)


@router.delete("/{application_id}", status_code=204)
async def delete(application_id: str, db: Db, user: User) -> Response:
    """Delete an application as an authorized administrator."""
    await mutate(ApplicationRepository(db), user, "delete", application_id)
    return Response(status_code=204)
