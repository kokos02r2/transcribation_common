import logging

from fastapi import FastAPI, HTTPException

from app.api.v1.routers import main_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from app.core.config import settings
from app.core.init_db import create_first_superuser
from fastapi.templating import Jinja2Templates
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title=settings.app_title, docs_url=None, redoc_url=None)
Instrumentator().instrument(app).expose(app)
logger = logging.getLogger(__name__)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(main_router)


@app.on_event("startup")
async def ensure_first_superuser_exists():
    await create_first_superuser()
    if settings.first_superuser_email:
        logger.info(
            "First superuser bootstrap checked for %s",
            settings.first_superuser_email,
        )


@app.get("/", response_class=RedirectResponse)
async def redirect_to_dashboard():
    return RedirectResponse(url="/dashboard")


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse(url="/login")

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

templates = Jinja2Templates(directory="app/templates")
