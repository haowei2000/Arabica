from pydantic import BaseModel


class GetIndicatorInfoRequestSchema(BaseModel):
    """Schema for getting indicator information request.

    获取指标信息请求的模式定义。

    Attributes:
        indicator_name (str): The name of the indicator to query. 要查询的指标名称。
    """

    indicator_name: str


class DimensionInfoSchema(BaseModel):
    """Schema for detailed information about dimensions.

    维度详细信息的模式定义。

    Attributes:
        name (str): The name of the dimension_registry. 维度名称。
        code (str): The code of the dimension_registry. 维度代码。
        alias (str): The alias of the dimension_registry. 维度别名。
    """

    name: str
    code: str
    alias: str | None = None


class TableSchema(BaseModel):
    """Schema for detailed information about tables.

    表详细信息的模式定义。

    Attributes:
        table_name (str): The name of the table. 表名称。
        table_description (str): The schema of the table. 表模式。
    """

    table_name: str
    table_description: str | None = None


class IndicatorInfoSchema(BaseModel):
    """Schema for detailed information about indicators.

    指标详细信息的模式定义。

    Attributes:
        name (str): The name of the indicator. 指标名称。
        sql_template (str): SQL template for the indicator. 指标的SQL模板。
        dimensions (list[DimensionInfoSchema]): List of dimensions associated with the indicator. 与指标关联的维度列表。
        related_tables (list[str]): List of tables related to the indicator. 与指标相关的表列表。
    """

    name: str
    sql_template: str
    dimensions: list[DimensionInfoSchema]
    related_tables: list[TableSchema]
