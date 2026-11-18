"""Define validated authentication request and public response contracts."""

from typing import Literal
from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    """Accept local credentials without accepting query operators."""
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    """Accept the current and replacement password."""
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=12, max_length=72)

    @field_validator("newPassword")
    @classmethod
    def validate_bytes(cls, value: str) -> str:
        """Enforce bcrypt's byte limit rather than silently truncate."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class LandingChange(BaseModel):
    """Restrict the default landing destination to supported choices."""
    defaultLandingPage: Literal["dashboard", "applications"]


class PublicUser(BaseModel):
    """Expose profile data without credential material."""
    id: str
    name: str
    email: str
    role: str
    portfolioAccess: list[str]
    status: str
    defaultLandingPage: Literal["dashboard", "applications"]
    mustChangePassword: bool


class LoginResponse(BaseModel):
    """Return a signed session and its public identity."""
    token: str
    user: PublicUser
