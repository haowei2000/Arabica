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
    superuser_bypass: bool = Field(
        default=True,
        description="Allow superusers to run without quota checks",
    )

    @property
    def default_free_tokens_per_user(self) -> int:
        """Return the configured default free token grant."""
        if self.free_tokens_per_user is not None:
            return self.free_tokens_per_user
        return 100_000
