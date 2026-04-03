from pydantic import BaseModel, Field

from structure.core.enums import InputFormat, OutputFormat


class ConvertRequest(BaseModel):
    """文件转换请求模型"""

    content: str = Field(
        description="要转换的文本内容",
        min_length=1,
        max_length=1000000,
        default="# Hello World\n\nThis is **bold** text.",
    )
    input_format: InputFormat = Field(
        default=InputFormat.markdown,
        description="输入格式，支持 md(Markdown) 或 txt(纯文本)",
    )
    output_format: OutputFormat = Field(
        ..., description="输出格式，支持 word(Word文档) 或 pdf(PDF文档)"
    )
    filename: str | None = Field(
        default="测试文档",
        description="输出文件名（不含扩展名），允许中文、字母、数字、下划线和中划线",
        max_length=255,
        pattern=r"^[a-zA-Z0-9_\-\u4e00-\u9fff]+$",
    )
