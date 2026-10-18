"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.errors import register_error_handlers
from app.routers import auth, dashboard, items, teams

app = FastAPI(title="Newtonite Ops API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(auth.router)
app.include_router(teams.router)
app.include_router(items.router)
app.include_router(dashboard.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness + database reachability, used by the docker compose healthcheck."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
