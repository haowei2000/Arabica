#!/usr/bin/env python3
"""
Celery Application Configuration

用于后台任务处理的 Celery 应用配置。
流式任务继续使用 Redis Streams Worker，非流式后台任务使用 Celery。

使用示例:
    # 启动 worker
    celery -A aiwen.workers.celery_app worker --loglevel=info

    # 启动 beat (定时任务)
    celery -A aiwen.workers.celery_app beat --loglevel=info

    # 启动 flower (监控面板)
    celery -A aiwen.workers.celery_app flower --port=5555
"""

import logging

from celery import Celery

from aiwen.config.factory import get_settings

logger = logging.getLogger(__name__)


def _build_redis_url() -> str:
    """构建 Redis URL"""
    settings = get_settings()
    redis_config = settings.redis

    if not redis_config:
        raise ValueError("Redis configuration is required for Celery")

    # 构建认证部分
    auth = ""
    if redis_config.username and redis_config.password:
        auth = f"{redis_config.username}:{redis_config.password}@"
    elif redis_config.password:
        auth = f":{redis_config.password}@"

    return f"redis://{auth}{redis_config.host}:{redis_config.port}/{redis_config.db}"


def create_celery_app() -> Celery:
    """创建并配置 Celery 应用"""
    redis_url = _build_redis_url()

    app = Celery(
        "aiwen",
        broker=redis_url,
        backend=redis_url,
        include=[
            "aiwen.workers.tasks.background_tasks",
        ],
    )

    # Celery 配置
    app.conf.update(
        # 任务序列化
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",

        # 时区
        timezone="Asia/Shanghai",
        enable_utc=True,

        # 任务配置
        task_track_started=True,
        task_time_limit=3600,  # 1小时超时
        task_soft_time_limit=3300,  # 55分钟软超时
        task_acks_late=True,  # 任务完成后再确认
        task_reject_on_worker_lost=True,

        # Worker 配置
        worker_prefetch_multiplier=4,
        worker_max_tasks_per_child=1000,  # 防止内存泄漏
        worker_disable_rate_limits=False,

        # 结果配置
        result_expires=86400,  # 结果保存24小时

        # 重试配置
        broker_connection_retry_on_startup=True,

        # 任务路由（可选）
        task_routes={
            "aiwen.workers.tasks.background_tasks.send_email": {"queue": "email"},
            "aiwen.workers.tasks.background_tasks.generate_report": {"queue": "report"},
            "aiwen.workers.tasks.background_tasks.*": {"queue": "default"},
        },

        # Beat 定时任务配置
        beat_schedule={
            # 示例：每小时清理过期数据
            # "cleanup-expired-data": {
            #     "task": "aiwen.workers.tasks.background_tasks.cleanup_expired_data",
            #     "schedule": crontab(minute=0),  # 每小时执行
            # },
        },
    )

    logger.info(f"Celery app configured with broker: {redis_url.split('@')[-1]}")
    return app


# 创建全局 Celery 实例
celery_app = create_celery_app()

# 用于 CLI 启动
app = celery_app
