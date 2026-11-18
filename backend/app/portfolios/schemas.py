"""Define portfolio request and response contracts."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PortfolioInput(BaseModel):
    """Validate editable portfolio fields."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    owner: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    productArea: str = Field(default="", max_length=200)
    status: Literal["Active", "Archived"] = "Active"


class Portfolio(PortfolioInput):
    """Expose a portfolio and its live application aggregates."""
    id: str
    applicationCount: int
    qualityScore: int | None
    canEdit: bool
    canDelete: bool


class PortfolioPage(BaseModel):
    """Return a bounded page and effective create permission."""
    items: list[Portfolio]
    total: int
    canCreate: bool


class ApplicationSummary(BaseModel):
    """Expose linked application fields for navigation."""
    id: str
    name: str
    productArea: str = ""
    qualityScore: int | None = None
    riskLevel: str = ""


class ApplicationPage(BaseModel):
    """Return a bounded page of linked applications."""
    items: list[ApplicationSummary]
    total: int
