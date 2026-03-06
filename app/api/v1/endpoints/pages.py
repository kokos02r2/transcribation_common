from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.core.users import current_superuser, current_user
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# Страница логина (доступна всем)
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# Защищённые страницы
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "username": user.email,
        "is_superuser": getattr(user, "is_superuser", False),
    })


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request, user: User = Depends(current_superuser)):
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "username": user.email,
    })


@router.get("/api_token", response_class=HTMLResponse)
async def api_token_page(request: Request, user: User = Depends(current_user)):
    return RedirectResponse(url="/tokens", status_code=307)


@router.get("/webhook_token", response_class=HTMLResponse)
async def webhook_token_page(request: Request, user: User = Depends(current_user)):
    return RedirectResponse(url="/tokens", status_code=307)


@router.get("/tokens", response_class=HTMLResponse)
async def tokens_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse("tokens.html", {
        "request": request,
        "username": user.email,
        "is_superuser": getattr(user, "is_superuser", False),
    })
