# schemas/response.py
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    """
    成功响应模型

    Attributes:
        code (int): 响应状态码，默认为200
        message (str): 响应消息，默认为"success"
        data (T | None): 响应数据，可以是任意类型
    """

    code: int = 200
    message: str = "success"
    data: T | None


class ErrorResponse(BaseModel):
    """
    错误响应模型

    Attributes:
        code (int): 错误状态码
        message (str): 错误消息
        detail (Any | None): 详细错误信息，可选
    """

    code: int
    message: str
    detail: Any | None = None  # 可选：调试信息（生产环境慎用）


# 快捷函数
def success(data: T, message: str = "success") -> SuccessResponse[T]:
    """
    创建成功响应的快捷函数

    Args:
        data (T): 要返回的数据
        message (str): 响应消息，默认为"success"

    Returns:
        SuccessResponse[T]: 包含数据的成功响应对象
    """
    return SuccessResponse(data=data, message=message)


def error(code: int, message: str, detail: Any = None) -> ErrorResponse:
    """
    创建错误响应的快捷函数

    Args:
        code (int): 错误状态码
        message (str): 错误消息
        detail (Any): 详细错误信息，可选

    Returns:
        ErrorResponse: 包含错误信息的错误响应对象
    """
    return ErrorResponse(code=code, message=message, detail=detail)
