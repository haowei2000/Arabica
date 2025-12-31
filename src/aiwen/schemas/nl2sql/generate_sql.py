from typing import Annotated, Any

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from aiwen.services.nl2sql.dimension_registry import get_registry


class DimensionList(list[str]):
    """
    动态维度列表：
    - core_schema: 等价于 list[str]
    - json_schema: items.enum 动态注入
    """

    @classmethod
    def __get_pydantic_core_schema__(
            cls,
            source_type: Any,
            handler,
    ) -> core_schema.CoreSchema:
        # 👇 关键：直接复用 list[str] 的 schema
        return handler.generate_schema(list[str])

    @classmethod
    def __get_pydantic_json_schema__(
            cls,
            core_schema: core_schema.CoreSchema,
            handler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)

        registry = get_registry()
        registered_dimensions = registry.list_dimensions()

        builtin_dimensions = ["时间"]
        all_dimensions = sorted(set(registered_dimensions) | set(builtin_dimensions))

        # 👇 注入 enum
        schema["items"] = {
            "type": "string",
            "enum": all_dimensions,
        }

        schema["description"] = "可用维度（动态注册）"

        return schema


class SqlResponse(BaseModel):
    sql: Annotated[str, "生成的SQL语句"]

    where_dimensions_name: Annotated[
        DimensionList,
        "SQL语句中的过滤维度名称列表,用于WHERE子句",
    ]

    groupby_dimensions_name: Annotated[
        DimensionList,
        "SQL语句中的分组维度名称列表,用于GROUP BY子句",
    ]
