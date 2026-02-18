import asyncio
import threading

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from aiwen.config.factory import get_settings
from aiwen.core.bootstrap import ApplicationBootstrap

# 全局变量存储资源
_worker_resources = threading.local()

celery_app = Celery("aiwen")
settings = get_settings()

# 配置 Celery
_redis_auth = f":{settings.redis.password}@" if settings.redis.password else ""
celery_app.conf.update(
    broker_url=f"redis://{_redis_auth}{settings.redis.host}:{settings.redis.port}/{settings.redis.db}",
    result_backend=f"redis://{_redis_auth}{settings.redis.host}:{settings.redis.port}/{settings.redis.db}",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
    # Task discovery
    imports=[
        "aiwen.celery_worker.tasks.document_tasks",
        "aiwen.celery_worker.tasks.run_history_tasks",
        "aiwen.celery_worker.tasks.context_sync_tasks",
    ],
)


@worker_process_init.connect
def init_worker(**kwargs):
    """Worker 进程初始化时执行"""
    print("Worker process initializing...")
    try:
        from aiwen.core.bootstrap import ApplicationBootstrap, bootstrap_celery

        bootstrap: ApplicationBootstrap = asyncio.run(bootstrap_celery())
        _worker_resources.bootstrap = bootstrap
        print("Worker resources initialized successfully")

    except Exception as e:
        print(f"Error initializing worker: {e}")
        raise


@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    """Worker 进程关闭时执行"""
    sig = kwargs.get("sig", "unknown")
    how = kwargs.get("how", "unknown")
    print(f"Worker shutting down, signal={sig}, how={how}")
    try:
        # 关闭 Redis 连接
        if hasattr(_worker_resources, "bootstrap"):
            assert isinstance(_worker_resources.bootstrap, ApplicationBootstrap)
            _worker_resources.bootstrap.cleanup()
            print("Bootstrap Cleaned")

        # 关闭数据库引擎（如果有需要）
        # 通常由全局清理处理

        print("Worker resources cleaned up")
    except Exception as e:
        print(f"Error during worker shutdown: {e}")


# 在任务中使用资源的示例
@celery_app.task
def example_task():
    """示例任务，演示如何在任务中使用资源"""
    # 从线程局部存储获取 Redis 客户端
    redis_client = getattr(_worker_resources, "redis_client", None)
    if redis_client:
        redis_client.incr("task_counter")

    return "Task completed"
