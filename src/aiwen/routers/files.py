from datetime import datetime
import logging
import urllib.parse

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from aiwen.schemas.common import ErrorResponse
from aiwen.schemas.files.convert import ConvertRequest
from aiwen.services.files.convert import converter_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nl2sql/files", tags=["files","nl2sql"])


@router.post(
    "/convert",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "成功转换文件",
            "content": {
                "application/pdf": {
                    "example": "PDF文件二进制内容"
                },
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "example": "Word文件二进制内容"
                }
            }
        },
        400: {
            "model": ErrorResponse,
            "description": "请求参数错误"
        },
        500: {
            "model": ErrorResponse,
            "description": "服务器内部错误"
        }
    },
    summary="转换文本内容",
    description="将Markdown或纯文本内容转换为Word或PDF格式"
)
async def convert_text(request: ConvertRequest):
    """
    转换文本内容为指定格式

    支持的转换:
    - Markdown → Word (推荐使用Pandoc)
    - Markdown → PDF
    - 纯文本 → Word
    - 纯文本 → PDF

    **参数说明:**
    - content: 要转换的文本内容（支持中英文）
    - input_format: 输入格式 (md 或 txt)
    - output_format: 输出格式 (word 或 pdf)
    - filename: 输出文件名，仅支持字母、数字、中文、下划线和连字符

    **返回:**
    文件流，浏览器会自动下载生成的文件

    **示例:**
    ```json
    {
      "content": "# 标题\\n\\n这是**粗体**文本",
      "input_format": "md",
      "output_format": "pdf",
      "filename": "我的文档"
    }
    ```
    """
    try:
        buffer, media_type, file_extension = converter_service.convert(
            content=request.content,
            input_format=request.input_format.value,
            output_format=request.output_format.value
        )

        filename = f"{request.filename}.{file_extension}"
        # 使用Latin-1编码安全的ASCII回退名称
        ascii_filename = f"{request.filename[:50]}.{file_extension}".encode('ascii', 'ignore').decode('ascii')
        # 对完整UTF-8文件名进行URL编码
        encoded_filename = urllib.parse.quote(filename)

        # 构造兼容的Content-Disposition头部
        content_disposition = f"attachment; filename=\"{ascii_filename}\"; filename*=utf-8''{encoded_filename}"

        return StreamingResponse(
            buffer,
            media_type=media_type,
            headers={
                "Content-Disposition": content_disposition,
                "X-Conversion-Time": datetime.now().isoformat(),
                "X-Content-Length": str(buffer.getbuffer().nbytes)
            }
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"转换失败: {e!s}"
        )
