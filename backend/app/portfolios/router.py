"""Expose scoped portfolio inventory endpoints."""

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, Response
from pymongo.asynchronous.database import AsyncDatabase
from app.auth.router import unrestricted_user
from app.core.database import get_db
from app.portfolios.repository import PortfolioRepository
from app.portfolios.schemas import ApplicationPage, Portfolio, PortfolioInput, PortfolioPage
from app.portfolios.service import detail, identifier, list_portfolios, mutate

router = APIRouter(prefix="/api/portfolios", tags=["Portfolios"])
Db = Annotated[AsyncDatabase, Depends(get_db)]
User = Annotated[dict, Depends(unrestricted_user)]


@router.get("", response_model=PortfolioPage)
async def listing(db: Db, user: User, search: str = Query("", max_length=200), status: Literal["Active", "Archived"] | None = None, productArea: str = Query("", max_length=200), limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List visible portfolios with optional filters."""
    return await list_portfolios(PortfolioRepository(db), user, search, status, productArea, limit, offset)


@router.post("", response_model=Portfolio, status_code=201)
async def create(body: PortfolioInput, db: Db, user: User) -> dict:
    """Create a portfolio for an authorized administrator."""
    return await mutate(PortfolioRepository(db), user, "create", body=body)


@router.get("/{portfolio_id}", response_model=Portfolio)
async def get(portfolio_id: str, db: Db, user: User) -> dict:
    """Return a visible portfolio and its aggregates."""
    return await detail(PortfolioRepository(db), user, portfolio_id)


@router.get("/{portfolio_id}/applications", response_model=ApplicationPage)
async def applications(portfolio_id: str, db: Db, user: User, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)) -> dict:
    """List linked applications after checking portfolio visibility."""
    repo = PortfolioRepository(db)
    await detail(repo, user, portfolio_id)
    return await repo.applications(identifier(portfolio_id), limit, offset)


@router.put("/{portfolio_id}", response_model=Portfolio)
async def update(portfolio_id: str, body: PortfolioInput, db: Db, user: User) -> dict:
    """Replace editable fields on an authorized portfolio."""
    return await mutate(PortfolioRepository(db), user, "edit", portfolio_id, body)


@router.delete("/{portfolio_id}", status_code=204)
async def delete(portfolio_id: str, db: Db, user: User) -> Response:
    """Delete an unreferenced portfolio as an administrator."""
    await mutate(PortfolioRepository(db), user, "delete", portfolio_id)
    return Response(status_code=204)
