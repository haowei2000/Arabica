import re

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator


class DescribableRequest(BaseModel):
    """
    请求模型，用于获取表描述数据

    Attributes:
        table_name (str): 表名称
    """
    table_name: str

    @field_validator('table_name')
    @classmethod
    def validate_table_name(cls, v):
        # Check length (typically identifiers should not be too long)
        if len(v) > 64:
            raise RequestValidationError('table name is too long')

        # Check character set - only allow alphanumeric and underscore
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise RequestValidationError('table name contains invalid characters')

        # Convert to lowercase to prevent case-sensitive issues
        return v.lower()


class DescribableResponse(BaseModel):
    """
    表描述模型，表示一个表的描述信息

    Attributes:
        table_name (str): 表名称
        description (str): 表描述
    """
    table_name: str
    description: str
