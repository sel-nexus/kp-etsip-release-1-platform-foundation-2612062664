"""Validate application inventory requests and responses."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

Score = Annotated[int, Field(strict=True, ge=0, le=100)]
Count = Annotated[int, Field(strict=True, ge=0)]


class ApplicationInput(BaseModel):
    """Describe the editable application inventory fields."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    portfolioId: str = Field(min_length=1, max_length=24)
    productArea: str = Field(default="", max_length=200)
    businessOwner: str = Field(default="", max_length=200)
    technologyOwner: str = Field(default="", max_length=200)
    etsOwner: str = Field(default="", max_length=200)
    criticality: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    tier: Annotated[int, Field(strict=True, ge=1, le=4)] = 1
    lifecycleStatus: Literal["Active", "Deprecated", "Retired"] = "Active"
    technologyStack: str = Field(default="", max_length=4000)
    repositoryLink: HttpUrl | None = None
    pipelineLink: HttpUrl | None = None
    applicationHealthScore: Score = 0
    qualityScore: Score = 0
    automationCoverage: Score = 0
    unitTestCoverage: Score = 0
    openDefects: Count = 0
    openCriticalDefects: Count = 0
    riskLevel: Literal["Low", "Medium", "High"] = "Low"

    @field_validator("portfolioId")
    @classmethod
    def valid_portfolio_id(cls, value: str) -> str:
        """Reject malformed references at the API boundary."""
        from bson import ObjectId
        if not ObjectId.is_valid(value):
            raise ValueError("Invalid portfolio ID")
        return str(ObjectId(value))


class Application(ApplicationInput):
    """Expose a record with its portfolio name and effective actions."""
    id: str
    portfolioName: str
    canEdit: bool
    canDelete: bool


class ApplicationPage(BaseModel):
    """Expose a bounded list and current create capability."""
    items: list[Application]
    total: int
    canCreate: bool
