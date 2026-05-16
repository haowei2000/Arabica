"""Quota routes for the authenticated user."""

from typing import Annotated

from fastapi import APIRouter, Depends

from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import QuotaServiceDep
from structure.schemas.auth.quota import QuotaResponse
from structure.schemas.auth.user import UserResponse
from structure.services.auth.quota_service import QuotaService

router = APIRouter(prefix="/quota", tags=["quota"])


@router.get("/me", response_model=QuotaResponse)
async def get_my_quota(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    quota_service: QuotaServiceDep,
) -> QuotaResponse:
    """Return the current user's quota counters."""
    quota = await quota_service.ensure_user_quota(current_user, auto_commit=True)
    return QuotaResponse(**QuotaService.quota_to_dict(quota))
