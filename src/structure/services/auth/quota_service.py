"""Quota management for default user free usage."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.base import AppSettings
from structure.config.factory import get_settings
from structure.models.auth.quota_ledger import QuotaLedger
from structure.models.auth.user import User
from structure.models.auth.user_quota import UserQuota
from structure.models.runs.run import Run


class QuotaExceededError(RuntimeError):
    """Raised when a user has no quota left for starting a run."""


class QuotaService:
    """Service for granting, checking, and consuming user token quota."""

    DEFAULT_FREE_TOKENS_PER_USER = 100_000
    FREE_GRANT_REASON = "default_free_grant"
    RUN_CONSUME_REASON = "run_token_consumed"

    def __init__(
        self,
        db_session: AsyncSession,
        settings: AppSettings | None = None,
    ) -> None:
        self.db = db_session
        self.settings = settings or get_settings()

    async def get_user_quota(
        self,
        user_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> UserQuota | None:
        """Fetch a user's quota row."""
        stmt = select(UserQuota).where(UserQuota.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def ensure_user_quota(
        self,
        user: User,
        *,
        auto_commit: bool = False,
        for_update: bool = False,
    ) -> UserQuota:
        """Create a quota row if missing and grant the default free quota."""
        quota = await self.get_user_quota(user.id, for_update=for_update)
        if quota is None:
            quota = UserQuota(user_id=user.id)
            self.db.add(quota)
            await self.db.flush()

        await self.grant_default_free_quota(user, quota=quota)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(quota)

        return quota

    def _configured_free_tokens_per_user(self) -> int:
        """Return the configured default free grant."""
        quota_settings = self.settings.quota
        configured = getattr(quota_settings, "default_free_tokens_per_user", None)
        if configured is None:
            configured = getattr(quota_settings, "free_tokens_per_user", None)
        if configured is None:
            configured = self.DEFAULT_FREE_TOKENS_PER_USER
        return int(configured)

    async def grant_default_free_quota(
        self,
        user: User,
        *,
        quota: UserQuota | None = None,
        auto_commit: bool = False,
    ) -> UserQuota | None:
        """Grant or top up the configured default free quota once per user."""
        if not self.settings.quota.enabled:
            return quota

        quota = quota or await self.get_user_quota(user.id, for_update=True)
        if quota is None:
            quota = UserQuota(user_id=user.id)
            self.db.add(quota)
            await self.db.flush()

        configured_grant = self._configured_free_tokens_per_user()
        if configured_grant <= 0 or quota.free_quota_total >= configured_grant:
            if auto_commit:
                await self.db.commit()
            return quota

        delta = configured_grant - quota.free_quota_total
        quota.free_quota_total += delta
        self.db.add(
            QuotaLedger(
                user_id=user.id,
                delta_tokens=delta,
                balance_after=quota.remaining_tokens,
                reason=self.FREE_GRANT_REASON,
                metadata_json={"source": "default_user_grant"},
            )
        )

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(quota)

        return quota

    async def grant_free_quota_if_eligible(
        self,
        user: User,
        *,
        quota: UserQuota | None = None,
        auto_commit: bool = False,
    ) -> UserQuota | None:
        """Backward-compatible wrapper for the default free quota grant."""
        return await self.grant_default_free_quota(
            user,
            quota=quota,
            auto_commit=auto_commit,
        )

    async def assert_can_start_run(self, user: User) -> UserQuota | None:
        """Ensure the user has quota before starting a run."""
        if not self.settings.quota.enabled:
            return None

        if self.settings.quota.superuser_bypass and getattr(
            user, "is_superuser", False
        ):
            return None

        quota = await self.ensure_user_quota(user, for_update=True)
        if quota.remaining_tokens <= 0:
            raise QuotaExceededError("Free quota exhausted. Add credits to continue.")

        return quota

    async def consume_run_tokens(
        self,
        run_id: str | UUID,
        *,
        input_tokens: int,
        output_tokens: int,
        auto_commit: bool = False,
    ) -> UserQuota | None:
        """Consume actual run token usage from the owning user's quota."""
        if not self.settings.quota.enabled:
            return None

        total_tokens = max(int(input_tokens), 0) + max(int(output_tokens), 0)
        if total_tokens <= 0:
            return None

        run_result = await self.db.execute(select(Run).where(Run.id == run_id))
        run = run_result.scalar_one_or_none()
        if run is None:
            return None

        user_result = await self.db.execute(select(User).where(User.id == run.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return None

        if self.settings.quota.superuser_bypass and getattr(
            user, "is_superuser", False
        ):
            return None

        quota = await self.ensure_user_quota(user, for_update=True)
        free_to_consume = min(total_tokens, quota.free_remaining)
        remaining_after_free = total_tokens - free_to_consume
        paid_to_consume = min(remaining_after_free, quota.paid_remaining)
        overrun_tokens = remaining_after_free - paid_to_consume

        quota.free_quota_used += free_to_consume + overrun_tokens
        quota.paid_quota_used += paid_to_consume
        self.db.add(
            QuotaLedger(
                user_id=user.id,
                run_id=run.id,
                delta_tokens=-total_tokens,
                balance_after=quota.remaining_tokens,
                reason=self.RUN_CONSUME_REASON,
                metadata_json={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "overrun_tokens": overrun_tokens,
                },
            )
        )

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(quota)

        return quota

    @staticmethod
    def quota_to_dict(quota: UserQuota) -> dict[str, Any]:
        """Serialize quota counters for API responses."""
        return {
            "free_quota_total": quota.free_quota_total,
            "free_quota_used": quota.free_quota_used,
            "paid_quota_total": quota.paid_quota_total,
            "paid_quota_used": quota.paid_quota_used,
            "remaining_tokens": quota.remaining_tokens,
            "period_start": quota.period_start,
            "period_end": quota.period_end,
        }
