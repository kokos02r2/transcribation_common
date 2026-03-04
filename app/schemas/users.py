from fastapi_users import schemas
from pydantic import BaseModel, EmailStr


class UserRead(schemas.BaseUser[int]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


class AdminUserCreateRequest(BaseModel):
    email: EmailStr
    password: str
    is_superuser: bool = False
