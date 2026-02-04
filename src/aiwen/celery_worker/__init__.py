#!/usr/bin/env python3
"""
Celery Module - Celery 任务队列模块

提供 Celery 应用配置和后台任务。

模块导出:
    - celery_app: Celery 应用实例
"""

from .celery_app import celery_app

__all__ = [
    "celery_app",
]
