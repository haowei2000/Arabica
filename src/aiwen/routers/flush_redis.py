#!/usr/bin/env python3
"""
模块名称: Redis缓存清理接口

功能描述:
    提供RESTful API接口用于手动清空Redis缓存，支持按模式匹配删除或清空所有缓存

作者: haowei
创建日期: 2025/12/8
最后修改: 2025/12/8 14:04
修改人员: haowei
版本: v1.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - fastapi
    - aiwen.middleware.cache_middleware

使用示例:
    POST /flush-redis-cache
    POST /flush-redis-cache?pattern=cache:user:*
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from aiwen.middleware.cache_middleware import clear_cache_pattern

router = APIRouter(prefix="/admin", tags=["Admin Operations"])


@router.post("/flush-redis-cache", summary="清空Redis缓存")
async def flush_redis_cache(
        pattern: str | None = Query(
            "cache:*",
            description="要清空的缓存键模式，例如: cache:* 表示清空所有缓存, cache:user:* 表示清空用户相关缓存"
        )
):
    """
    清空Redis缓存接口
    
    Args:
        pattern (str, optional): 要清空的缓存键模式，默认为 "cache:*" 清空所有缓存
        
    Returns:
        dict: 包含清除结果的信息
        
    Raises:
        HTTPException: 当Redis未初始化或清空过程中发生错误时抛出异常
    """
    try:
        deleted_count = await clear_cache_pattern(pattern)
        return {
            "success": True,
            "input": f"成功清空 {deleted_count} 条缓存记录",
            "pattern": pattern
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Redis未初始化: {e!s}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空缓存时发生错误: {e!s}")
