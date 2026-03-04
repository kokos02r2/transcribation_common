from fastapi import APIRouter, Depends, HTTPException

from fastapi_users import InvalidPasswordException
from fastapi_users.exceptions import UserAlreadyExists

from app.core.users import auth_backend, current_superuser, fastapi_users, get_user_manager
from app.schemas.users import AdminUserCreateRequest, UserCreate, UserRead, UserUpdate

router = APIRouter()

router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix='/auth/jwt',
    tags=['auth'],
)
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix='/users',
    tags=['users'],
)


@router.post("/admin/users", response_model=UserRead, tags=["users"])
async def admin_create_user(
    payload: AdminUserCreateRequest,
    _: UserRead = Depends(current_superuser),
    user_manager=Depends(get_user_manager),
):
    try:
        user_create = UserCreate(
            email=payload.email,
            password=payload.password,
            is_active=True,
            is_superuser=payload.is_superuser,
            is_verified=True,
        )
        user = await user_manager.create(user_create, safe=False, request=None)
        return user
    except UserAlreadyExists:
        raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует")
    except InvalidPasswordException as exc:
        raise HTTPException(status_code=400, detail=exc.reason)
