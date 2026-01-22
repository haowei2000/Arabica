#!/usr/bin/env python3
"""
Celery CLI - Celery 命令行入口

提供便捷的 Celery worker/beat/flower 启动命令。

使用示例:
    # 启动 worker
    aiwen-celery worker

    # 启动 worker（指定并发数和队列）
    aiwen-celery worker --concurrency=4 --queues=default,email

    # 启动 beat
    aiwen-celery beat

    # 启动 flower
    aiwen-celery flower --port=5555
"""

import click


@click.group()
def cli():
    """Aiwen Celery CLI - 后台任务管理"""
    pass


@cli.command()
@click.option("--concurrency", "-c", default=4, help="Worker 并发数")
@click.option("--queues", "-Q", default="default", help="监听的队列（逗号分隔）")
@click.option("--loglevel", "-l", default="info", help="日志级别")
def worker(concurrency: int, queues: str, loglevel: str):
    """启动 Celery Worker"""
    from aiwen.workers.celery_app import celery_app

    celery_app.worker_main(
        argv=[
            "worker",
            f"--concurrency={concurrency}",
            f"--queues={queues}",
            f"--loglevel={loglevel}",
        ]
    )


@cli.command()
@click.option("--loglevel", "-l", default="info", help="日志级别")
def beat(loglevel: str):
    """启动 Celery Beat（定时任务调度器）"""
    from aiwen.workers.celery_app import celery_app

    celery_app.worker_main(
        argv=[
            "beat",
            f"--loglevel={loglevel}",
        ]
    )


@cli.command()
@click.option("--port", "-p", default=5555, help="Flower 端口")
def flower(port: int):
    """启动 Flower（Celery 监控面板）"""
    from aiwen.workers.celery_app import celery_app

    celery_app.worker_main(
        argv=[
            "flower",
            f"--port={port}",
        ]
    )


def main():
    """CLI 入口"""
    cli()


if __name__ == "__main__":
    main()
