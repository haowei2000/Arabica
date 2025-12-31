from collections.abc import Callable, Sequence
import datetime
import logging
import re

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.mes.indicator import (
    AiDictDimension,
    AiIndicatorDimensionInfo,
    AiIndicatorInfo,
)
from aiwen.schemas.nl2sql.sub_dimension import SingleDimension
from aiwen.services.nl2sql.dimension_registry import get_registry

logger = logging.getLogger(__name__)


def _normalize_dimension_name(name: str) -> str:
    """
    规范化维度名称，用于匹配比较。

    移除"维"和"名称"等后缀，统一格式以便匹配。

    Args:
        name: 原始维度名称

    Returns:
        规范化后的维度名称
    """
    normalized = name.strip()
    # 移除"维"后缀
    if normalized.endswith("维"):
        normalized = normalized[:-1]
    # 移除"名称"后缀
    if normalized.endswith("名称"):
        normalized = normalized[:-2]
    return normalized


def _find_dimension_getter(dimension_name: str, registry) -> tuple[str | None, Callable | None]:
    """
    在注册表中查找维度的值获取函数。

    支持灵活匹配：
    - 精确匹配
    - 添加/删除"维"后缀
    - 添加/删除"名称"后缀

    Args:
        dimension_name: 要查找的维度名称
        registry: 维度注册表实例

    Returns:
        匹配的维度名和获取函数的元组，如果未找到则返回 (None, None)
    """
    all_getters = registry._all_values_getters

    # 1. 精确匹配（最快）
    if dimension_name in all_getters:
        return dimension_name, all_getters[dimension_name]

    # 2. 生成可能的变体并尝试匹配
    variants = {dimension_name}  # 使用 set 避免重复

    # 添加/删除"维"字的变体
    if dimension_name.endswith("维"):
        variants.add(dimension_name[:-1])
    else:
        variants.add(dimension_name + "维")

    # 添加/删除"名称"的变体
    if "名称" in dimension_name:
        variants.add(dimension_name.replace("名称", ""))
    else:
        variants.add(dimension_name + "名称")

    # 尝试所有变体
    for variant in variants:
        if variant in all_getters:
            return variant, all_getters[variant]

    # 3. 规范化匹配（移除"维"和"名称"后比较核心名称）
    normalized_input = _normalize_dimension_name(dimension_name)

    for reg_name, getter_func in all_getters.items():
        normalized_reg = _normalize_dimension_name(reg_name)
        if normalized_input == normalized_reg:
            return reg_name, getter_func

    # 未找到匹配
    return None, None


async def _get_column_values_set(
    table: str, column: str, session: AsyncSession
) -> list | None:
    """
    使用SQLAlchemy从数据库中获取某个表的某个列的所有值的列表

    Args:
        table: 表名
        column: 列名
        session: 异步数据库会话

    Returns:
        包含该列所有值的列表，如果出错返回None
    """
    try:
        # 使用传入的session执行查询
        result = await session.execute(
            text(f"SELECT DISTINCT `{column}` FROM `{table}`")
        )

        # 获取所有结果并转换为列表
        values_list = [row[0] for row in result if row[0] is not None]

        return values_list

    except Exception as e:
        logger.error(f"获取列值列表时出错: {e}")
        return None




async def _get_allowed_values(
    dimension_snippet: str,
    sql_text: str,
    session: AsyncSession,
    dimension_name: str | None = None,
) -> list:
    """
    从维度的SQL片段中提取允许的值集合

    优先使用维度注册表获取值，如果注册表中没有该维度，则从数据库直接查询。

    Args:
        dimension_snippet: 维度的SQL片段字符串
        sql_text: 完整的SQL文本
        session: 数据库会话
        dimension_name: 维度名称（可选），如果提供且在注册表中，将使用注册表获取值

    Returns:
        允许的值集合
    """
    # 1. 首先尝试从维度注册表获取值
    if dimension_name:
        registry = get_registry()
        matched_name, getter_func = _find_dimension_getter(dimension_name, registry)

        if getter_func:
            try:
                logger.info(
                    f"使用维度注册表获取 '{dimension_name}' 的允许值 "
                    f"(匹配到注册名称: '{matched_name}')"
                )
                # 调用 getter 函数（异步）
                allowed_values = await getter_func()
                logger.info(f"从注册表获取到 {len(allowed_values)} 个值")
                return allowed_values
            except Exception as e:
                logger.warning(
                    f"从维度注册表获取 '{dimension_name}' 的值时出错: {e}，将回退到数据库查询"
                )

    # 2. 回退到原来的实现：从数据库直接查询
    try:
        # 从dimension_snippet中提取列名
        # 支持格式: "列名" 或 "表别名.列名"
        if "." in dimension_snippet:
            # 如果有别名，提取列名
            parts = dimension_snippet.split(".", 1)
            column_name = parts[1].strip()
        else:
            column_name = dimension_snippet.strip()

        # 从SQL中提取表名
        # 匹配 FROM table 或 FROM table AS alias 或 FROM table alias
        table_pattern = r"FROM\s+([`\w.]+)(?:\s+(?:AS\s+)?(\w+))?"
        matches = re.findall(table_pattern, sql_text, re.IGNORECASE)

        if not matches:
            logger.warning("无法从SQL中提取表名: %s", sql_text)
            return []

        # 使用第一个表名
        table_name = matches[0][0].strip("`")

        logger.info(f"从数据库表 '{table_name}' 查询列 '{column_name}' 的允许值")
        allowed_values = await _get_column_values_set(table_name, column_name, session)

        # 过滤掉日期/时间类型的值
        if allowed_values and any(
            isinstance(item, (datetime.date, datetime.datetime))
            for item in allowed_values
        ):
            logger.info("检测到日期/时间类型，返回空列表")
            return []

        logger.info(f"从数据库获取到 {len(allowed_values) if allowed_values else 0} 个值")
        return allowed_values or []

    except Exception as e:
        logger.warning(
            "提取允许的值集合时出错: %s 可选值设置为空 请检查指标维度配置 %s",
            e,
            dimension_snippet,
        )
        return []


async def _get_dimension_by_code(
    dimension_code: str, session: AsyncSession
) -> AiDictDimension | None:
    query = select(AiDictDimension).where(
        and_(
            AiDictDimension.dimension_code == dimension_code,
            AiDictDimension.is_delete == 0,
        )
    )
    execute_result = await session.scalars(query)
    dimensions = execute_result.all()
    if len(dimensions) >= 1:
        logger.warning("维度编码 %s 对应多个，将随机选择第一个值", dimension_code)
        return dimensions[0]
    if len(dimensions) == 0:
        logger.error("维度编码 %s 未找到对应维度", dimension_code)
        return None
    return dimensions[0]


async def indi_r_sub_dim(
    session: AsyncSession,
    indicator_name: str | None = None,
    indicator_gid: str | None = None,
) -> list[SingleDimension] | None:
    """
    查询当前指标所有的下级维度
    :param session: 异步数据库会话
    :param indicator_name: 指标名称（字符串）
    :param indicator_gid: 指标GID（字符串）
    :return: 指标图谱字典，或 None（若指标不存在/已删除）
    """
    # 1. 验证主指标是否存在
    if not await _validate_indicator(session, indicator_name, indicator_gid):
        logger.error("验证错误")
        raise ValueError("指标不存在或已删除")
    # 2. 获得主指标的GID
    if indicator_gid is not None:
        indicator_name = await _get_indicator_name(session, indicator_gid)
    sql_text, indicator_gid = await _get_indicator(session, indicator_name)
    logger.info("主指标的id:%s", indicator_gid)

    # 3. 查找下级关联维度
    sub_indicator_dimension = await _get_sub_dimension(session, indicator_gid)
    logger.info("下级维度:%s", sub_indicator_dimension)

    # 4. 为每个子维度添加允许的值和gid
    result = []
    for item in sub_indicator_dimension:
        allowed_values = await _get_allowed_values(
            item["sql_snippet"],
            sql_text,
            session,
            dimension_name=item["dimension_name"],  # 传入维度名称以使用注册表
        )
        dimension = await _get_dimension_by_code(item["dimension_code"], session)
        if dimension:
            result.append(
                SingleDimension(
                    dimension_name=item["dimension_name"],
                    dimension_code=item["dimension_code"],
                    dimension_gid=str(dimension.gid),
                    allowed_values=allowed_values,
                )
        )
    return result


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


async def _get_indicator_name(session: AsyncSession, indicator_gid: str) -> str | None:
    """获取指标的名称"""
    query = select(AiIndicatorInfo.name).where(AiIndicatorInfo.gid == indicator_gid)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def _get_indicator(
    session: AsyncSession, indicator_name: str
) -> tuple[str, str] | None:
    """获取指标的SQL文本和GID"""
    query = select(AiIndicatorInfo.sql_text, AiIndicatorInfo.gid).where(
        AiIndicatorInfo.name == indicator_name,
        AiIndicatorInfo.is_delete == 0,
    )
    result = await session.execute(query)
    row = result.fetchone()
    if row:
        return (row.sql_text, row.gid)  # 返回 sql_text 和 gid
    return None


async def _get_sub_dimension(
    session: AsyncSession, indicator_gid: str, exclude_dimensions_code=None
) -> list[dict]:
    """获取子维度的GID"""
    if exclude_dimensions_code is None:
        exclude_dimensions_code = ["SJ", ""]
    query = select(
        AiIndicatorDimensionInfo,
    ).where(
        AiIndicatorDimensionInfo.indicator_id == indicator_gid
        and AiIndicatorInfo.is_delete == 0
    )
    result = await session.scalars(query)
    indicator_dim_infos: Sequence[AiIndicatorDimensionInfo] = result.all()
    return [
        {
            "dimension_name": item.dimension_name,
            "dimension_code": item.dimension_code,
            "sql_snippet": item.sql_snippet,
        }
        for item in indicator_dim_infos
        if item.dimension_code is not None
        and item.dimension_code not in exclude_dimensions_code
    ]
