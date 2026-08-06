from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.repository import initialize_state

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        initialize_state(db, settings)
    yield


app = FastAPI(
    title="Sibyl Trace API",
    version=settings.app_version,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Sibyl-Admin-Token", "X-Sibyl-Gateway-Secret"],
    )
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "sibyl-trace", "version": settings.app_version}
