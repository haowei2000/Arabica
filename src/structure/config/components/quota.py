# structure/config/components/quota.py
from pydantic import BaseModel, Field


class QuotaConfig(BaseModel):
    """User quota settings."""

    enabled: bool = Field(default=True, description="Enable user token quotas")
    free_tokens_per_user: int | None = Field(
        default=None,
        ge=0,
        description="Default free token quota granted to each user",
    )
    free_tokens_per_verified_email: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Deprecated fallback for free_tokens_per_user. Kept for existing "
            "deployments using QUOTA__FREE_TOKENS_PER_VERIFIED_EMAIL."
        ),
    )
    superuser_bypass: bool = Field(
        default=True,
        description="Allow superusers to run without quota checks",
    )

    @property
    def default_free_tokens_per_user(self) -> int:
        """Return the configured default free token grant."""
        if self.free_tokens_per_user is not None:
            return self.free_tokens_per_user
        if self.free_tokens_per_verified_email is not None:
            return self.free_tokens_per_verified_email
        return 100_000
