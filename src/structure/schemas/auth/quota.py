"""Quota API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class QuotaResponse(BaseModel):
    """Current quota counters for the authenticated user."""

    free_quota_total: int = Field(..., description="Granted free tokens")
    free_quota_used: int = Field(..., description="Consumed free tokens")
    paid_quota_total: int = Field(..., description="Purchased tokens")
    paid_quota_used: int = Field(..., description="Consumed paid tokens")
    remaining_tokens: int = Field(..., description="Remaining usable tokens")
    period_start: datetime | None = Field(None, description="Quota period start")
    period_end: datetime | None = Field(None, description="Quota period end")
