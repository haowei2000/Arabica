from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.services.nl2sql.get_indicator_info import _get_table_schema


async def get_table_schema(table_name: str, db: AsyncSession) -> str | None:
    """
    获取指定表的简洁结构字符串，包括列名、类型、主键、非空和注释
    忽略列名、类型、注释全为空的列
    格式：列名 类型 [PK] [NN] [注释]，逗号分隔
    """
    return await _get_table_schema(table_name, db)
