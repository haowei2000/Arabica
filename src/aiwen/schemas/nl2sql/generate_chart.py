from enum import Enum
from typing import Any

from plotly.graph_objs import Figure
from pydantic import BaseModel, ConfigDict


class ReturnTypeEnum(str, Enum):
    PNG = "png"
    HTML = "html"


class ChartRequestSchema(BaseModel):
    """图表数据点模型"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: list[dict[str, Any]]
    title: str | None = None
    width: int = 1000
    height: int = 600
    show: bool = True
    save_path: str | None = None
    return_type: ReturnTypeEnum = ReturnTypeEnum.HTML


class ChartResponseSchema(BaseModel):
    """图表响应模型"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    chart: str | bytes | None | Figure
