#!/usr/bin/env python3
"""
FastAPI 应用启动脚本

功能描述:
    启动 FastAPI 应用服务器。
    使用统一的 AppSettings 管理配置，不再直接加载环境变量。

运行方式:
    structure-api
    或
    python -m structure.api_cli

作者: wanghaowei
创建日期: 2025/11/10
最后修改: 2025/12/31
修改人员: haowei
版本: v2.0.0
"""

import os

import uvicorn

# 配置会在 structure.config.factory 模块导入时自动加载
# 导入 app 时会自动触发配置加载
from structure.app import app


def run():
    """Run the FastAPI application."""
    # Trust X-Forwarded-* from the reverse proxy in front (Cloudflare,
    # frontend nginx, etc.) so request.url.scheme reflects the public
    # scheme rather than the in-cluster HTTP hop.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("STRUCTURE_APP_PORT", 8000)),
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
