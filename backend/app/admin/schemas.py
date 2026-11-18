"""Validate administration requests and constrain role vocabulary."""

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

RoleName = Literal['Administrator', 'Executive', 'Portfolio Leader', 'Engineer', 'Auditor', 'Read Only']
Permission = Literal['view', 'create', 'edit', 'delete', 'export', 'administer']


class UserInput(BaseModel):
    """Describe editable local account fields."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    role: RoleName
    portfolioAccess: list[str] = Field(default_factory=list, max_length=1000)
    status: Literal['Active', 'Inactive'] = 'Active'
    managerName: str | None = Field(default=None, max_length=200)
    vendorIndicator: bool = False
    password: str | None = Field(default=None, min_length=12, max_length=72)

    @field_validator('name')
    @classmethod
    def name_valid(cls, value: str) -> str:
        """Require a nonblank display name."""
        value = value.strip()
        if not value:
            raise ValueError('Name is required')
        return value

    @field_validator('email')
    @classmethod
    def email_valid(cls, value: str) -> str:
        """Normalize and validate local email addresses."""
        value = value.lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address')
        return value

    @field_validator('password')
    @classmethod
    def password_bytes(cls, value: str | None) -> str | None:
        """Enforce bcrypt's byte limit."""
        if value is not None and len(value.encode('utf-8')) > 72:
            raise ValueError('Password must be at most 72 UTF-8 bytes')
        return value


class RoleInput(BaseModel):
    """Describe one of the supported roles and its permissions."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: RoleName
    description: str = Field(default='', max_length=500)
    permissions: list[Permission] = Field(default_factory=list, max_length=6)

    @field_validator('permissions')
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        """Reject duplicate permission entries."""
        if len(set(value)) != len(value):
            raise ValueError('Permissions must be unique')
        return value
