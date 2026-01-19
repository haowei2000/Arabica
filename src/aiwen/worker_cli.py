#!/usr/bin/env python3
"""
Agent Worker CLI - Celery Worker Launcher

Starts the Celery worker for processing agent tasks.

Usage:
    aiwen-worker
    or
    python -m aiwen.worker_cli
    or directly with celery:
    celery -A aiwen.celery_app worker --loglevel=info
"""

import logging
import sys

import click

from aiwen.extensions.logger import setup_logging

logger = logging.getLogger(__name__)


def _run_celery_worker(
    loglevel: str = "info",
    concurrency: int = 4,
    queues: str = "celery_agent_tasks",
):
    """Start Celery worker."""
    from aiwen.celery_app import celery_app

    # Build worker arguments
    argv = [
        "worker",
        f"--loglevel={loglevel}",
        f"--concurrency={concurrency}",
        f"--queues={queues}",
    ]

    logger.info("=" * 60)
    logger.info("Starting Celery Worker...")
    logger.info(f"  Log level: {loglevel}")
    logger.info(f"  Concurrency: {concurrency}")
    logger.info(f"  Queues: {queues}")
    logger.info("=" * 60)

    # Start Celery worker
    # Worker process initialization (Agent Registry, etc.) is handled
    # by the worker_process_init signal in celery_app.py
    celery_app.worker_main(argv)


@click.command()
@click.option(
    "--loglevel",
    default="info",
    type=click.Choice(["debug", "info", "warning", "error", "critical"]),
    help="Logging level",
)
@click.option(
    "--concurrency",
    default=4,
    type=int,
    help="Number of worker processes",
)
@click.option(
    "--queues",
    default="celery_agent_tasks",
    help="Comma-separated list of queues to consume from",
)
def main(loglevel: str, concurrency: int, queues: str):
    """Start the Agent Worker (Celery)."""
    # Setup logging first
    setup_logging()

    try:
        # Start Celery worker
        _run_celery_worker(
            loglevel=loglevel,
            concurrency=concurrency,
            queues=queues,
        )

    except KeyboardInterrupt:
        logger.info("Worker stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error in worker: {e}", exc_info=True)
        sys.exit(1)


def main_sync():
    """Synchronous entry point for CLI."""
    main()


if __name__ == "__main__":
    main_sync()
