from collections.abc import Sequence
import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.mes.indicator import AiIndicatorInfo, AiIndicatorRelation

logger = logging.getLogger(__name__)


async def indi_r_sub_indi(
    session: AsyncSession,
    indicator_name: str | None = None,
    indicator_gid: str | None = None,
) -> list | None:
    """
    查询当前指标的所有的子指标
    :param session: 异步数据库会话
    :param indicator_name: 指标名称（字符串）
    :param indicator_gid: 指标GID（字符串）
    :return: 指标图谱字典，或 None（若指标不存在/已删除）
    """

    # 1. 验证主指标是否存在,或name和gid是否一致
    if not await _validate_indicator(session, indicator_name, indicator_gid):
        logger.info("验证错误")
        return None
    # 2. 获得主指标的GID
    if indicator_gid is None:
        indicator_gid = await _get_indicator_gid(session, indicator_name)
        logger.info("主指标的id:%s", indicator_gid)
    # 3. 查找下级指标
    sub_indicator_gid = await _get_sub_indicator_gid(session, indicator_gid)
    logger.info("下级指标的id:%s", sub_indicator_gid)
    # 4. 查找下级指标名称
    sub_indicator_name = await _get_sub_indicator_name(session, sub_indicator_gid)
    logger.info("下级指标的名称:%s", sub_indicator_name)
    return [{"indicator_gid":gid,"indicator_name":name} for name, gid in zip(sub_indicator_name, sub_indicator_gid, strict=False)]


async def _validate_indicator(
    session: AsyncSession, indicator_name: str | None, indicator_gid: str | None
) -> bool:
    """验证指标是否存在且有效"""
    if indicator_name is not None:
        query = select(AiIndicatorInfo.gid).where(
            and_(
                AiIndicatorInfo.name == indicator_name,
                AiIndicatorInfo.is_delete == 0,
                AiIndicatorInfo.is_active_raw == 0,
            )
        )
        result = await session.execute(query)
        if result.scalar_one_or_none() is None:
            raise ValueError(f"指标{indicator_name}不存在或已删除")
    if indicator_gid is not None:
        query = select(AiIndicatorInfo.gid).where(
            and_(
                AiIndicatorInfo.gid == indicator_gid,
                AiIndicatorInfo.is_delete == 0,
                AiIndicatorInfo.is_active_raw == 0,
            )
        )
        result = await session.execute(query)
        if result.scalar_one_or_none() is None:
            raise ValueError(f"指标{indicator_gid}不存在或已删除")
    if indicator_name is not None and indicator_gid is not None:
        query = select(AiIndicatorInfo.gid).where(
            and_(
                AiIndicatorInfo.name == indicator_name,
                AiIndicatorInfo.gid == indicator_gid,
                AiIndicatorInfo.is_delete == 0,
                AiIndicatorInfo.is_active_raw == 0,
            )
        )
        result = await session.execute(query)
        if result.scalar_one_or_none() is None:
            raise ValueError(f"指标{indicator_name}和{indicator_gid}不匹配或已删除")
    return True


async def _get_indicator_gid(session: AsyncSession, indicator_name: str) -> str | None:
    """获取指标的GID"""
    query = select(AiIndicatorInfo.gid).where(AiIndicatorInfo.name == indicator_name)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def _get_sub_indicator_gid(
    session: AsyncSession, indicator_gid: str
) -> list[str | None]:
    """获取子指标的GID"""
    query = select(AiIndicatorRelation.sub_indicator_id).where(
        AiIndicatorRelation.p_indicator_id == indicator_gid
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def _get_sub_indicator_name(
    session: AsyncSession, sub_indicator_gid: list
) -> list:
    """获取子指标的名称"""
    query = select(AiIndicatorInfo.name).where(
        AiIndicatorInfo.gid.in_(sub_indicator_gid)
    )
    result = await session.execute(query)
    return [name for name in result.scalars().all() if name is not None]
