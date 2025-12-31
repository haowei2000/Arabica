#!/usr/bin/env python3
"""
模块名称: 指标信息获取服务

功能描述:
    该模块提供获取指标详细信息的服务，包括指标的基本信息、维度信息以及相关数据表结构。
    主要功能包括：
    1. 根据指标名称查询指标详情
    2. 获取指标关联的维度信息
    3. 解析指标SQL并提取相关数据表
    4. 获取数据表的结构信息

作者: haowei
创建日期: 2025/11/29
最后修改: 2025/11/29 18:16
修改人员: haowei
版本: 1.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - sqlalchemy: 数据库异步操作
    - aiwen.models.mes.indicator: 指标数据模型
    - aiwen.schemas.nl2sql.indicator_info: 指标信息数据结构

使用示例:
    async with get_readonly_session("mes") as db:
        result = await get_indicator_info_service("设备开机率（生产）", db)
        print(result)
"""

import logging
import re

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_readonly_session
from aiwen.models.mes.indicator import AiIndicatorDimensionInfo, AiIndicatorInfo
from aiwen.schemas.nl2sql.indicator_info import (
    DimensionInfoSchema,
    IndicatorInfoSchema,
    TableSchema,
)

logger = logging.getLogger(__name__)


async def _get_table_schema(table_name: str, db: AsyncSession) -> str | None:
    """
    返回表的简洁结构字符串，包括列名、类型、主键、非空和注释
    忽略列名、类型、注释全为空的列
    格式：列名 类型 [PK] [NN] [注释]，逗号分隔
    """
    if table_name.lower() == "dual":
        return ""

    # 支持 schema.table
    if "." in table_name:
        schema, tbl = table_name.split(".", 1)
        safe_name = f"`{schema.replace('`', '``')}`.`{tbl.replace('`', '``')}`"
    else:
        safe_name = f"`{table_name.replace('`', '``')}`"

    stmt = text(f"SHOW FULL COLUMNS FROM {safe_name}")

    try:
        result = await db.execute(stmt)
        rows = result.fetchall()
        if not rows:
            return ""

        parts = []
        for row in rows:
            col_name = row[0] or ""
            col_type = row[1] or ""
            nullable = row[2] or ""
            key = row[3] or ""
            comment = row[8] or ""

            # 如果列名、类型、注释全为空，忽略
            if not (col_name.strip() or col_type.strip() or comment.strip()):
                continue
            if col_name.lower().startswith("uda") or col_name.lower().startswith(
                "data_role"
            ):
                continue
            s = f"{col_name} {col_type}"
            if key == "PRI":
                s += " PK"
            if nullable == "NO":
                s += " NN"
            if comment:
                s += f" [{comment}]"
            parts.append(s)

        return ", ".join(parts)

    except Exception as e:
        logger.error(f"获取表 {table_name} 简洁结构失败: {e}")
        return ""


def _find_table_name_from_sql(sql: str) -> list[str]:
    """从SQL语句中提取完全限定的表名。

    返回按出现顺序排列的唯一表名列表。支持使用反引号或双引号的可选引号标识符,
    以及像`db`.`schema`.`table`或schema.table这样的限定名称。

    Args:
        sql (str): 输入的SQL语句

    Returns:
        list[str]: 按出现顺序排列的唯一表名列表
    """
    if not sql:
        return []

    # remove string literals to avoid false positives
    sql_clean = re.sub(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", " ", sql)

    # capture identifiers that appear after common clauses
    pattern = re.compile(
        r'(?i)\b(?:from|join|into|update|table|delete\s+from|truncate\s+table)\s+([`"]?[\w$]+[`"]?(?:\.[`"]?[\w$]+[`"]?){0,2})'
    )
    raw_matches = pattern.findall(sql_clean)

    def normalize(name: str) -> str:
        parts = [p.strip('`"') for p in name.split(".")]
        return ".".join(parts)

    seen = set()
    result = []
    for m in raw_matches:
        # handle comma-separated lists like "FROM a, b"
        for part in re.split(r"\s*,\s*", m):
            norm = normalize(part)
            if norm and norm.lower() not in seen:
                seen.add(norm.lower())
                result.append(norm)

    # 过滤掉常见的虚拟表和临时表
    filtered_result = [table for table in result if not table.lower().startswith(('dual', 'temp_', 'tmp_'))]
    return filtered_result


async def get_indicator_info_service(
    indicator_name: str, db: AsyncSession
) -> IndicatorInfoSchema:
    """Retrieve detailed information about indicators.

    获取指标的详细信息，包括名称、描述、数据类型等。

    Args:
        indicator_name (str): 指标名称
        db (AsyncSession): 数据库会话

    Returns:
        IndicatorInfoSchema: 指标详细信息对象

    Raises:
        ValueError: 当指标不存在或SQL文本为空时抛出
    """
    stmt = (
        select(AiIndicatorInfo)
        .where(AiIndicatorInfo.name == indicator_name, AiIndicatorInfo.is_delete == 0)
        .limit(1)
    )
    indicator = await db.scalar(stmt)
    if not indicator:
        raise ValueError(f"指标 {indicator_name} 不存在")
    dim_stmt = select(AiIndicatorDimensionInfo).where(
        AiIndicatorDimensionInfo.indicator_id == indicator.gid,
        AiIndicatorDimensionInfo.is_delete == 0,
    )
    dim_result = await db.execute(dim_stmt)
    dim_list: list[AiIndicatorDimensionInfo] = list(dim_result.scalars().all())
    dimensions = [
        DimensionInfoSchema(
            name=str(dim.dimension_name),
            code=str(dim.dimension_code),
            alias=str(dim.sql_snippet) if dim.sql_snippet else None,
        )
        for dim in dim_list
    ]
    if not indicator.sql_text:
        raise ValueError(f"指标 {indicator_name} 的 SQL 文本为空")
    logger.debug(f"Indicator SQL Text: {indicator.sql_text}")
    related_tables = _find_table_name_from_sql(str(indicator.sql_text))
    table_schemas = [
        TableSchema(
            table_name=table_name, table_description=await _get_table_schema(table_name, db)
        )
        for table_name in related_tables
    ]
    return IndicatorInfoSchema(
        name=str(indicator.name),
        sql_template=str(indicator.sql_text),
        dimensions=dimensions,
        related_tables=table_schemas,
    )


if __name__ == "__main__":
    import asyncio

    load_dotenv()

    async def main():
        async with get_readonly_session("mes") as db:
            result = await get_indicator_info_service("设备开机率（生产）", db)
            print(result)

    asyncio.run(main())
