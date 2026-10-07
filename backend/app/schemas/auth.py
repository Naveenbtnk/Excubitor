from pydantic import BaseModel

from app.schemas.common import Email, OrmModel


class LoginRequest(BaseModel):
    email: Email
    password: str


class UserRead(OrmModel):
    id: int
    email: Email
    name: str
    role: str


class AuthResponse(BaseModel):
    user: UserRead
