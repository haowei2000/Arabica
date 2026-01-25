#!/usr/bin/env python3
"""
Celery CLI - Celery 命令行入口

提供便捷的 Celery worker/beat/flower 启动命令。
"""

import subprocess
import sys

import click


def _normalize_queues(queues: str) -> str:
    return ",".join([q.strip() for q in queues.split(",") if q.strip()]) or "default"


@click.group()
def cli():
    """Aiwen Celery CLI - 后台任务管理"""
    pass


@cli.command()
@click.option("--concurrency", "-c", default=4, show_default=True, help="Worker 并发数")
@click.option("--queues", "-Q", default="celery", show_default=True, help="监听的队列（逗号分隔）")
@click.option("--loglevel", "-l", default="info", show_default=True, help="日志级别")
@click.option("--hostname", default=None, help="Worker hostname")
@click.option("--pool", "-P", default="prefork", show_default=True, help="Pool类型: prefork/solo/gevent/eventlet")
def worker(concurrency: int, queues: str, loglevel: str, hostname: str | None, pool: str):
    """启动 Celery Worker"""
    queues = _normalize_queues(queues)

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "aiwen.workers.celery_app",
        "worker",
        f"--concurrency={concurrency}",
        f"--queues={queues}",
        f"--loglevel={loglevel}",
        f"--pool={pool}",
    ]

    if hostname:
        cmd.append(f"--hostname={hostname}")

    subprocess.run(cmd)


@cli.command()
@click.option("--loglevel", "-l", default="info", show_default=True, help="日志级别")
def beat(loglevel: str):
    """启动 Celery Beat（定时任务调度器）"""
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "aiwen.workers.celery_app",
        "beat",
        f"--loglevel={loglevel}",
    ]

    subprocess.run(cmd)


@cli.command()
@click.option("--port", "-p", default=5555, show_default=True, help="Flower 端口")
@click.option("--address", "-a", default="0.0.0.0", show_default=True, help="Flower 监听地址")
def flower(port: int, address: str):
    """启动 Flower（Celery 监控面板）"""
    try:
        import flower  # noqa: F401
    except ImportError:
        click.echo("Error: Flower is not installed. Install it with: uv add flower")
        raise SystemExit(1)

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "aiwen.workers.celery_app",
        "flower",
        f"--port={port}",
        f"--address={address}",
    ]

    subprocess.run(cmd)


def main():
    cli()


if __name__ == "__main__":
    main()
