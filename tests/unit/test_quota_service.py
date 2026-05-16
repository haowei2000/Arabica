"""Unit tests for quota service accounting."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from structure.models.auth.user_quota import UserQuota
from structure.services.auth.quota_service import QuotaExceededError, QuotaService
from tests.unit.routers.conftest import RUN_ID, USER_ID


def result_for(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def quota_settings():
    return SimpleNamespace(
        quota=SimpleNamespace(
            enabled=True,
            free_tokens_per_user=100_000,
            superuser_bypass=True,
        )
    )


@pytest.mark.asyncio
async def test_ensure_user_quota_grants_default_to_unverified_user():
    db = AsyncMock()
    db.add = MagicMock()
    quota = UserQuota(
        user_id=USER_ID,
        free_quota_total=0,
        free_quota_used=0,
        paid_quota_total=0,
        paid_quota_used=0,
    )
    db.execute.return_value = result_for(quota)
    user = MagicMock(id=USER_ID, email_verified=False, is_superuser=False)

    updated = await QuotaService(db, quota_settings()).ensure_user_quota(user)

    assert updated is quota
    assert quota.free_quota_total == 100_000
    assert quota.remaining_tokens == 100_000
    ledger = db.add.call_args.args[0]
    assert ledger.delta_tokens == 100_000
    assert ledger.balance_after == 100_000
    assert ledger.reason == QuotaService.FREE_GRANT_REASON
    assert ledger.metadata_json == {"source": "default_user_grant"}


@pytest.mark.asyncio
async def test_ensure_user_quota_is_idempotent_when_default_already_granted():
    db = AsyncMock()
    db.add = MagicMock()
    quota = UserQuota(
        user_id=USER_ID,
        free_quota_total=100_000,
        free_quota_used=10_000,
        paid_quota_total=0,
        paid_quota_used=0,
    )
    db.execute.return_value = result_for(quota)
    user = MagicMock(id=USER_ID, email_verified=False, is_superuser=False)

    updated = await QuotaService(db, quota_settings()).ensure_user_quota(user)

    assert updated is quota
    assert quota.free_quota_total == 100_000
    assert quota.remaining_tokens == 90_000
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_user_quota_tops_up_existing_user_to_default():
    db = AsyncMock()
    db.add = MagicMock()
    quota = UserQuota(
        user_id=USER_ID,
        free_quota_total=25_000,
        free_quota_used=10_000,
        paid_quota_total=0,
        paid_quota_used=0,
    )
    db.execute.return_value = result_for(quota)
    user = MagicMock(id=USER_ID, email_verified=False, is_superuser=False)

    updated = await QuotaService(db, quota_settings()).ensure_user_quota(user)

    assert updated is quota
    assert quota.free_quota_total == 100_000
    assert quota.remaining_tokens == 90_000
    ledger = db.add.call_args.args[0]
    assert ledger.delta_tokens == 75_000
    assert ledger.balance_after == 90_000

@pytest.mark.asyncio
async def test_consume_run_tokens_records_usage():
    db = AsyncMock()
    db.add = MagicMock()
    run = MagicMock(id=RUN_ID, user_id=USER_ID)
    user = MagicMock(id=USER_ID, email_verified=True, is_superuser=False)
    quota = UserQuota(
        user_id=USER_ID,
        free_quota_total=100_000,
        free_quota_used=10_000,
        paid_quota_total=0,
        paid_quota_used=0,
    )
    db.execute.side_effect = [
        result_for(run),
        result_for(user),
        result_for(quota),
    ]

    updated = await QuotaService(db, quota_settings()).consume_run_tokens(
        RUN_ID,
        input_tokens=1_000,
        output_tokens=2_000,
    )

    assert updated is quota
    assert quota.free_quota_used == 13_000
    assert quota.remaining_tokens == 87_000
    ledger = db.add.call_args.args[0]
    assert ledger.delta_tokens == -3_000
    assert ledger.balance_after == 87_000


@pytest.mark.asyncio
async def test_assert_can_start_run_rejects_exhausted_quota():
    db = AsyncMock()
    quota = UserQuota(
        user_id=USER_ID,
        free_quota_total=100_000,
        free_quota_used=100_000,
        paid_quota_total=0,
        paid_quota_used=0,
    )
    db.execute.return_value = result_for(quota)
    user = MagicMock(id=USER_ID, email_verified=True, is_superuser=False)

    with pytest.raises(QuotaExceededError):
        await QuotaService(db, quota_settings()).assert_can_start_run(user)
