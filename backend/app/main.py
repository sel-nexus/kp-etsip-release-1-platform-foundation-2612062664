"""Create the API and manage its MongoDB lifecycle."""

from contextlib import asynccontextmanager
import os
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.auth.router import router
from app.portfolios.router import router as portfolio_router
from app.applications.router import router as application_router
from app.dashboard.router import router as dashboard_router
from app.admin.router import router as admin_router
from app.audit.router import router as audit_router
from app.reports.router import router as reports_router
from app.core.config import Settings
from app.core.database import create_client
from app.core.seed import seed_database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application with an isolated lifecycle and explicit settings."""
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Open the shared client and initialize indexes before serving."""
        config = settings or Settings()
        client = create_client(config.mongodb_uri)
        try:
            application.state.settings = config
            application.state.db = client[config.mongodb_db]
            await client.admin.command("ping")
            await application.state.db.users.create_index("email", unique=True)
            await application.state.db.roles.create_index("name", unique=True)
            await application.state.db.portfolios.create_index("name", unique=True)
            await application.state.db.applications.create_index("portfolioId")
            await application.state.db.applications.create_index("name", unique=True)
            if config.seed_on_startup:
                await seed_database(application.state.db, config)
            yield
        finally:
            await client.close()

    application = FastAPI(title="KP ETSIP API", version="1.0", lifespan=lifespan)
    origins_text = settings.cors_origins if settings else os.environ.get("CORS_ORIGINS", dotenv_values(".env").get("CORS_ORIGINS") or "http://localhost:5173")
    origins = origins_text.split(",")
    application.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in origins], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
    application.include_router(router)
    application.include_router(portfolio_router)
    application.include_router(application_router)
    application.include_router(dashboard_router)
    application.include_router(admin_router)
    application.include_router(audit_router)
    application.include_router(reports_router)

    @application.get("/api/health", tags=["Health"])
    async def health() -> dict[str, str]:
        """Report process liveness without querying external dependencies."""
        return {"status": "ok"}

    return application


app = create_app()
