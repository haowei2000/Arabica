"""
测试数据构建器模块

提供 Builder 模式的测试数据构造器，简化测试数据的创建
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


class BaseBuilder:
    """基础构建器类"""

    def __init__(self):
        self._data = {}

    def build(self) -> dict[str, Any]:
        """构建并返回数据字典"""
        return self._data.copy()

    def build_dict(self) -> dict[str, Any]:
        """构建并返回数据字典（别名）"""
        return self.build()


class IndicatorBuilder(BaseBuilder):
    """指标信息构建器

    使用示例:
        >>> indicator = (
        ...     IndicatorBuilder()
        ...     .with_name("设备开机率")
        ...     .with_sql_template("SELECT * FROM equipment")
        ...     .with_dimension("时间", "SJ", "time_field")
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "name": "默认指标",
            "sql_template": "SELECT 1",
            "dimensions": [],
            "related_tables": [],
        }

    def with_name(self, name: str) -> "IndicatorBuilder":
        """设置指标名称"""
        self._data["name"] = name
        return self

    def with_sql_template(self, sql_template: str) -> "IndicatorBuilder":
        """设置 SQL 模板"""
        self._data["sql_template"] = sql_template
        return self

    def with_dimension(self, name: str, code: str, alias: str) -> "IndicatorBuilder":
        """添加一个维度"""
        dimension = {"name": name, "code": code, "alias": alias}
        self._data["dimensions"].append(dimension)
        return self

    def with_dimensions(self, dimensions: list[dict[str, str]]) -> "IndicatorBuilder":
        """设置多个维度"""
        self._data["dimensions"] = dimensions
        return self

    def with_table(
        self, table_name: str, schema_name: str | None = None
    ) -> "IndicatorBuilder":
        """添加一个关联表"""
        table = {"table_name": table_name, "schema_name": schema_name}
        self._data["related_tables"].append(table)
        return self

    def with_tables(self, tables: list[dict[str, Any]]) -> "IndicatorBuilder":
        """设置多个关联表"""
        self._data["related_tables"] = tables
        return self

    def with_description(self, description: str) -> "IndicatorBuilder":
        """设置描述"""
        self._data["description"] = description
        return self

    def with_code(self, code: str) -> "IndicatorBuilder":
        """设置指标代码"""
        self._data["code"] = code
        return self

    @classmethod
    def default(cls) -> "IndicatorBuilder":
        """创建一个默认的指标构建器"""
        return cls()

    @classmethod
    def production_indicator(cls) -> "IndicatorBuilder":
        """创建一个产量指标"""
        return (
            cls()
            .with_name("产量统计")
            .with_code("PROD_001")
            .with_sql_template(
                "SELECT SUM(quantity) as total FROM production WHERE date >= :start_date"
            )
            .with_dimension("时间", "SJ", "production.date")
            .with_dimension("产线", "CX", "production.line")
            .with_table("production", None)
        )

    @classmethod
    def equipment_rate_indicator(cls) -> "IndicatorBuilder":
        """创建一个设备开机率指标"""
        return (
            cls()
            .with_name("设备开机率（生产）")
            .with_code("EQU_001")
            .with_sql_template("SELECT AVG(uptime_rate) as rate FROM equipment_runtime")
            .with_dimension("时间", "SJ", "equipment_runtime.record_time")
            .with_table("equipment_runtime", None)
        )

    @classmethod
    def scrap_rate_indicator(cls) -> "IndicatorBuilder":
        """创建一个报废率指标"""
        return (
            cls()
            .with_name("累计报废率")
            .with_code("SCRAP_001")
            .with_sql_template(
                """
                SELECT ROUND(s1.报废数 / s2.总生产数, 2) as 累计报废率
                FROM (
                    SELECT IFNULL(SUM(pumo.报废数), 0) as 报废数
                    FROM pv_uex_making_order pumo
                    WHERE pumo.生产状态 = '完工'
                ) s1,
                (
                    SELECT IFNULL(SUM(pumo.报废数 + pumo.良品数 + pumo.不良品数), 1) as 总生产数
                    FROM pv_uex_making_order pumo
                    WHERE pumo.生产状态 = '完工'
                ) s2
                """
            )
            .with_dimension("时间", "SJ", "pumo.完工时间")
            .with_table("pv_uex_making_order", None)
        )


class ChartDataBuilder(BaseBuilder):
    """图表数据构建器

    使用示例:
        >>> chart_data = (
        ...     ChartDataBuilder()
        ...     .with_title("月度产量")
        ...     .add_data_point("一月", 100)
        ...     .add_data_point("二月", 150)
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "title": "图表标题",
            "data": [],
            "chart_type": "auto",
        }

    def with_title(self, title: str) -> "ChartDataBuilder":
        """设置图表标题"""
        self._data["title"] = title
        return self

    def with_chart_type(self, chart_type: str) -> "ChartDataBuilder":
        """设置图表类型"""
        self._data["chart_type"] = chart_type
        return self

    def add_data_point(self, name: str, value: Any) -> "ChartDataBuilder":
        """添加一个数据点"""
        self._data["data"].append({"name": name, "value": value})
        return self

    def with_data(self, data: list[dict[str, Any]]) -> "ChartDataBuilder":
        """设置完整的数据列表"""
        self._data["data"] = data
        return self

    def with_x_axis(self, x_axis: str) -> "ChartDataBuilder":
        """设置 X 轴标签"""
        self._data["x_axis"] = x_axis
        return self

    def with_y_axis(self, y_axis: str) -> "ChartDataBuilder":
        """设置 Y 轴标签"""
        self._data["y_axis"] = y_axis
        return self

    @classmethod
    def line_chart(cls) -> "ChartDataBuilder":
        """创建折线图数据"""
        return (
            cls()
            .with_title("趋势分析")
            .with_chart_type("line")
            .add_data_point("第1周", 100)
            .add_data_point("第2周", 120)
            .add_data_point("第3周", 110)
            .add_data_point("第4周", 140)
        )

    @classmethod
    def bar_chart(cls) -> "ChartDataBuilder":
        """创建柱状图数据"""
        return (
            cls()
            .with_title("对比分析")
            .with_chart_type("bar")
            .add_data_point("产品A", 85)
            .add_data_point("产品B", 92)
            .add_data_point("产品C", 78)
        )

    @classmethod
    def pie_chart(cls) -> "ChartDataBuilder":
        """创建饼图数据"""
        return (
            cls()
            .with_title("占比分析")
            .with_chart_type("pie")
            .add_data_point("优良", 70)
            .add_data_point("合格", 20)
            .add_data_point("不合格", 10)
        )


class SqlResponseBuilder(BaseBuilder):
    """SQL 响应构建器

    使用示例:
        >>> sql_response = (
        ...     SqlResponseBuilder()
        ...     .with_sql("SELECT * FROM users WHERE id = 1")
        ...     .with_parameters({"id": 1})
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "sql": "SELECT 1",
            "parameters": {},
        }

    def with_sql(self, sql: str) -> "SqlResponseBuilder":
        """设置 SQL 语句"""
        self._data["sql"] = sql
        return self

    def with_parameters(self, parameters: dict[str, Any]) -> "SqlResponseBuilder":
        """设置 SQL 参数"""
        self._data["parameters"] = parameters
        return self

    def with_parameter(self, key: str, value: Any) -> "SqlResponseBuilder":
        """添加单个 SQL 参数"""
        self._data["parameters"][key] = value
        return self

    def with_explanation(self, explanation: str) -> "SqlResponseBuilder":
        """设置 SQL 解释"""
        self._data["explanation"] = explanation
        return self

    @classmethod
    def select_query(cls) -> "SqlResponseBuilder":
        """创建一个 SELECT 查询"""
        return (
            cls()
            .with_sql(
                "SELECT * FROM production WHERE date >= :start_date AND date <= :end_date"
            )
            .with_parameter("start_date", "2024-01-01")
            .with_parameter("end_date", "2024-01-31")
        )

    @classmethod
    def aggregate_query(cls) -> "SqlResponseBuilder":
        """创建一个聚合查询"""
        return (
            cls()
            .with_sql(
                "SELECT COUNT(*) as total, AVG(value) as avg_value FROM metrics WHERE type = :type"
            )
            .with_parameter("type", "production")
        )


class GraphNodeBuilder(BaseBuilder):
    """图节点构建器

    使用示例:
        >>> node = (
        ...     GraphNodeBuilder()
        ...     .with_name("设备A")
        ...     .with_type(1)
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "id": str(uuid.uuid4()),
            "name": "默认节点",
            "type": 1,
            "code": None,
            "description": None,
        }

    def with_id(self, node_id: str) -> "GraphNodeBuilder":
        """设置节点 ID"""
        self._data["id"] = node_id
        return self

    def with_name(self, name: str) -> "GraphNodeBuilder":
        """设置节点名称"""
        self._data["name"] = name
        return self

    def with_type(self, node_type: int) -> "GraphNodeBuilder":
        """设置节点类型"""
        self._data["type"] = node_type
        return self

    def with_code(self, code: str) -> "GraphNodeBuilder":
        """设置节点代码"""
        self._data["code"] = code
        return self

    def with_description(self, description: str) -> "GraphNodeBuilder":
        """设置节点描述"""
        self._data["description"] = description
        return self

    @classmethod
    def indicator_node(cls) -> "GraphNodeBuilder":
        """创建指标节点"""
        return cls().with_name("产量指标").with_type(1).with_code("PROD_001")

    @classmethod
    def table_node(cls) -> "GraphNodeBuilder":
        """创建表节点"""
        return cls().with_name("生产表").with_type(2).with_code("production")


class GraphEdgeBuilder(BaseBuilder):
    """图边构建器

    使用示例:
        >>> edge = (
        ...     GraphEdgeBuilder()
        ...     .from_node("node1")
        ...     .to_node("node2")
        ...     .with_relation("depends_on")
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "source": None,
            "target": None,
            "relation": "related_to",
        }

    def from_node(self, source: str) -> "GraphEdgeBuilder":
        """设置源节点"""
        self._data["source"] = source
        return self

    def to_node(self, target: str) -> "GraphEdgeBuilder":
        """设置目标节点"""
        self._data["target"] = target
        return self

    def with_relation(self, relation: str) -> "GraphEdgeBuilder":
        """设置关系类型"""
        self._data["relation"] = relation
        return self

    def with_properties(self, properties: dict[str, Any]) -> "GraphEdgeBuilder":
        """设置边属性"""
        self._data["properties"] = properties
        return self


class ApiResponseBuilder(BaseBuilder):
    """API 响应构建器

    使用示例:
        >>> response = (
        ...     ApiResponseBuilder()
        ...     .success()
        ...     .with_data({"id": 1, "name": "test"})
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {
            "code": 200,
            "input": "Success",
            "data": None,
        }

    def success(self, message: str = "Success") -> "ApiResponseBuilder":
        """设置为成功响应"""
        self._data["code"] = 200
        self._data["input"] = message
        return self

    def error(self, code: int, message: str) -> "ApiResponseBuilder":
        """设置为错误响应"""
        self._data["code"] = code
        self._data["input"] = message
        return self

    def with_code(self, code: int) -> "ApiResponseBuilder":
        """设置响应码"""
        self._data["code"] = code
        return self

    def with_message(self, message: str) -> "ApiResponseBuilder":
        """设置响应消息"""
        self._data["input"] = message
        return self

    def with_data(self, data: Any) -> "ApiResponseBuilder":
        """设置响应数据"""
        self._data["data"] = data
        return self

    @classmethod
    def success_response(cls, data: Any = None) -> "ApiResponseBuilder":
        """创建成功响应"""
        return cls().success().with_data(data)

    @classmethod
    def error_response(
        cls, code: int = 500, message: str = "Error"
    ) -> "ApiResponseBuilder":
        """创建错误响应"""
        return cls().error(code, message)


class DatabaseRecordBuilder(BaseBuilder):
    """数据库记录构建器

    使用示例:
        >>> record = (
        ...     DatabaseRecordBuilder()
        ...     .with_field("id", 1)
        ...     .with_field("name", "test")
        ...     .with_timestamp()
        ...     .build()
        ... )
    """

    def __init__(self):
        super().__init__()
        self._data = {}

    def with_field(self, key: str, value: Any) -> "DatabaseRecordBuilder":
        """添加一个字段"""
        self._data[key] = value
        return self

    def with_fields(self, fields: dict[str, Any]) -> "DatabaseRecordBuilder":
        """添加多个字段"""
        self._data.update(fields)
        return self

    def with_id(self, record_id: Any = None) -> "DatabaseRecordBuilder":
        """添加 ID 字段"""
        self._data["id"] = record_id or uuid.uuid4()
        return self

    def with_timestamp(
        self, created: bool = True, updated: bool = True
    ) -> "DatabaseRecordBuilder":
        """添加时间戳字段"""
        now = datetime.utcnow().isoformat()
        if created:
            self._data["created_at"] = now
        if updated:
            self._data["updated_at"] = now
        return self

    def with_is_deleted(self, is_deleted: bool = False) -> "DatabaseRecordBuilder":
        """添加删除标记字段"""
        self._data["is_delete"] = 1 if is_deleted else 0
        return self
