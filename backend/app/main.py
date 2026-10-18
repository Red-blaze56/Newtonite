"""FastAPI application entry point: create the app and plug in each feature's router.

Features live in their own packages (auth, items, notifications) on top of shared plumbing
in core. Dependencies only point one way: core <- auth <- items -> notifications.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.all_models  # noqa: F401  (registers every table before the first query)
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.db import engine
from app.core.errors import register_error_handlers
from app.items.router import router as items_router
from app.notifications.router import router as notifications_router

app = FastAPI(title="Newtonite Ops API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(auth_router)
app.include_router(items_router)
app.include_router(notifications_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness + database reachability, used by the docker compose healthcheck."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
