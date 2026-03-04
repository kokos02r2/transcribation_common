from fastapi import FastAPI, HTTPException

from app.api.v1.routers import main_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from app.core.config import settings
from fastapi.templating import Jinja2Templates
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title=settings.app_title, docs_url=None, redoc_url=None)
Instrumentator().instrument(app).expose(app)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(main_router)


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
