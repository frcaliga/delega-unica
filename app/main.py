from fastapi import FastAPI

from app.config import get_settings
from app.routers import auth, tenant, users


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(tenant.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
