import logging
import uuid

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator

logger =logging.getLogger("schema")
class GetGraphRequest(BaseModel):
    """
    请求模型，用于获取图数据

    Attributes:
        indicator_id (str): 指标ID，必须是有效的UUID字符串
    """
    indicator_id: str | None = None

    @field_validator('indicator_id')
    @classmethod
    def validate_uuid(cls, v):
        """
        验证indicator_id是否为有效的UUID

        Args:
            v (str): 待验证的indicator_id值

        Returns:
            str: 验证通过的UUID字符串

        Raises:
            RequestValidationError: 当提供的值不是有效UUID时抛出
        """
        if v is not None:
            try:
                uuid.UUID(str(v))
                return v
            except Exception as e:
                raise RequestValidationError(f'indicator_id must be a valid UUID {e}')
        else:
            logger.info("Skip UUID validation for None value")
            return v


class Node(BaseModel):
    """
    图节点模型，表示一个指标节点

    Attributes:
        id (str): 节点ID
        name (str): 节点名称
        type (str): 节点类型
        code (str): 节点编码
        description (str): 节点描述
    """
    id: str
    name: str
    type: int|None
    code: str|None
    description: str|None

    class Config:
        extra = "allow"

class Edge(BaseModel):
    """
    图边模型，表示节点间的关系

    Attributes:
        source (str): 源节点ID
        target (str): 目标节点ID
        relation (str): 关系类型
    """
    source: str
    target: str
    relation: str | None

    class Config:
        extra = "allow"

class GraphResponse(BaseModel):
    """
    图数据响应模型

    Attributes:
        nodes (list[Node]): 节点列表
        edges (list[dict]): 边列表，每条边表示节点间的关系
    """
    nodes: list[Node]
    edges: list[Edge]
