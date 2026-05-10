#!/usr/bin/env python3
"""
Agent Worker 启动脚本

功能描述:
    初始化并启动 Agent Worker，包括数据库连接、Redis 客户端和 AgentRegistry 初始化。
    支持运行多个 Worker 实例实现并行处理。

运行方式:
    structure-worker                    # 启动单个 worker
    structure-worker -n 4               # 启动 4 个 worker
    structure-worker --workers 4        # 同上
    structure-worker --name my-worker   # 指定 worker 名称前缀

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/31
修改人员: haowei
版本: v3.0.0

"""

import asyncio
import logging
import os
import signal
import sys
from typing import Any
import uuid

import click

# 配置会在 structure.config.factory 模块导入时自动加载
from structure.core.bootstrap import bootstrap_worker
from structure.extensions.database import get_session
from structure.services.events.event_worker import Worker
from structure.services.executor.runtime import ExecutorInstanceManager
from structure.services.workspaces.workspace_crud import WorkspaceCRUD

logger = logging.getLogger(__name__)


def generate_consumer_name(prefix: str, workspace_id: str, index: int) -> str:
    """Generate unique consumer name for a worker."""
    hostname = os.environ.get("HOSTNAME", "local")
    short_uuid = uuid.uuid4().hex[:8]
    # Include workspace_id in consumer name for easier tracking
    ws_prefix = workspace_id[:8]
    return f"{prefix}-{ws_prefix}-{hostname}-{os.getpid()}-{index}-{short_uuid}"


_WORKSPACE_POLL_INTERVAL_SECONDS = 30


async def _list_active_workspace_ids() -> list[str]:
    """Return all active workspace IDs."""
    async with get_session("structure") as db:
        workspace_ids = await WorkspaceCRUD(db).get_all_active_ids()
    return list(workspace_ids)


async def _wait_for_workspaces(label: str = "Worker") -> list[str]:
    """Poll the DB until at least one active workspace exists, then return its IDs.

    Replaces the previous ``await asyncio.sleep(float("inf"))`` pattern: that
    blocked the event loop indefinitely on a fresh deploy with no workspaces
    yet, and SIGTERM could not interrupt cleanly. Polling in finite chunks
    keeps the loop responsive to signals and lets the worker pick up the first
    workspace as soon as it is created.
    """
    while True:
        workspace_ids = await _list_active_workspace_ids()
        if workspace_ids:
            logger.info(f"📋 [{label}] Found {len(workspace_ids)} active workspace(s)")
            return workspace_ids

        logger.info(
            "⏳ [%s] No active workspaces yet, retrying in %ds...",
            label,
            _WORKSPACE_POLL_INTERVAL_SECONDS,
        )
        await asyncio.sleep(_WORKSPACE_POLL_INTERVAL_SECONDS)


def _raise_if_worker_failed(tasks: list[asyncio.Task]) -> None:
    """Raise if any worker task finished with an exception."""
    for task in tasks:
        if not task.done():
            continue

        exception = task.exception()
        if exception:
            raise exception


def _start_missing_worker_tasks(
    *,
    redis_client: Any,
    tasks: list[asyncio.Task],
    started_keys: set[tuple[str, int]],
    workspace_ids: list[str],
    num_workers_per_workspace: int,
    name_prefix: str,
    shared_runtime: ExecutorInstanceManager,
    task_name_prefix: str,
) -> int:
    """Start worker tasks for active workspaces that are not being watched yet."""
    created = 0
    for workspace_id in workspace_ids:
        for worker_index in range(num_workers_per_workspace):
            key = (workspace_id, worker_index)
            if key in started_keys:
                continue

            consumer_name = generate_consumer_name(
                name_prefix,
                workspace_id,
                worker_index,
            )
            task = asyncio.create_task(
                run_single_worker(
                    redis_client,
                    get_session("structure"),
                    workspace_id,
                    consumer_name,
                    worker_index,
                    shared_runtime,
                ),
                name=f"{task_name_prefix}-{workspace_id[:8]}-{worker_index}",
            )
            tasks.append(task)
            started_keys.add(key)
            created += 1
    return created


async def _supervise_workspace_workers(
    *,
    redis_client: Any,
    tasks: list[asyncio.Task],
    started_keys: set[tuple[str, int]],
    num_workers_per_workspace: int,
    name_prefix: str,
    shared_runtime: ExecutorInstanceManager,
    label: str,
    task_name_prefix: str,
) -> None:
    """Keep worker tasks in sync with active workspaces.

    Workspaces can be created after the API or standalone worker has started.
    Polling lets the worker subscribe to those new workspace streams without
    requiring a process restart.
    """
    while True:
        _raise_if_worker_failed(tasks)

        workspace_ids = await _list_active_workspace_ids()
        created = _start_missing_worker_tasks(
            redis_client=redis_client,
            tasks=tasks,
            started_keys=started_keys,
            workspace_ids=workspace_ids,
            num_workers_per_workspace=num_workers_per_workspace,
            name_prefix=name_prefix,
            shared_runtime=shared_runtime,
            task_name_prefix=task_name_prefix,
        )
        if created:
            watched_workspaces = len({workspace_id for workspace_id, _ in started_keys})
            logger.info(
                "[%s] Added %s worker(s); watching %s active workspace(s)",
                label,
                created,
                watched_workspaces,
            )

        await asyncio.sleep(_WORKSPACE_POLL_INTERVAL_SECONDS)


async def run_single_worker(
    redis_client: Any,
    db_factory: Any,
    workspace_id: str,
    consumer_name: str,
    worker_index: int,
    shared_runtime: ExecutorInstanceManager,
) -> None:
    """Run a single worker instance."""
    logger.info(
        f"🚀 Worker [{worker_index}] for workspace [{workspace_id}] starting (consumer: {consumer_name})"
    )

    try:
        async with db_factory as session:
            worker = Worker(
                redis_client,
                session,
                consumer_name=consumer_name,
                runtime=shared_runtime,
            )
            await worker.start(workspace_id)
    except asyncio.CancelledError:
        logger.info(
            f"⏹️  Worker [{worker_index}] for workspace [{workspace_id}] cancelled"
        )
        raise
    except Exception as e:
        logger.error(
            f"❌ Worker [{worker_index}] for workspace [{workspace_id}] error: {e}",
            exc_info=True,
        )
        raise


async def run_workers(num_workers_per_workspace: int, name_prefix: str) -> None:
    """Run multiple workers concurrently for each active workspace."""
    logger.info("=" * 60)
    logger.info("🔧 Agent Worker Starting...")
    logger.info(f"   Workers per workspace: {num_workers_per_workspace}")
    logger.info("=" * 60)

    bootstrap = None
    tasks: list[asyncio.Task] = []
    supervisor_task: asyncio.Task | None = None

    try:
        # Initialize bootstrap
        bootstrap = await bootstrap_worker()
        redis_client = bootstrap.get_redis_client()

        # Single shared runtime so all workers can find executors created by any peer
        shared_runtime = ExecutorInstanceManager()

        # Get all active workspace IDs.  On a fresh deployment this list may be
        # empty — we poll until at least one workspace appears so the worker
        # starts as soon as it has work to do, instead of blocking on
        # ``sleep(float("inf"))`` (which made the event loop appear hung and
        # ate SIGTERM on container shutdown).
        workspace_ids = await _wait_for_workspaces()

        started_keys: set[tuple[str, int]] = set()
        _start_missing_worker_tasks(
            redis_client=redis_client,
            tasks=tasks,
            started_keys=started_keys,
            workspace_ids=workspace_ids,
            num_workers_per_workspace=num_workers_per_workspace,
            name_prefix=name_prefix,
            shared_runtime=shared_runtime,
            task_name_prefix="worker",
        )

        logger.info("=" * 60)
        logger.info(
            f"🚀 Started {len(tasks)} total worker(s) across {len(workspace_ids)} workspace(s)"
        )
        logger.info("=" * 60)

        supervisor_task = asyncio.create_task(
            _supervise_workspace_workers(
                redis_client=redis_client,
                tasks=tasks,
                started_keys=started_keys,
                num_workers_per_workspace=num_workers_per_workspace,
                name_prefix=name_prefix,
                shared_runtime=shared_runtime,
                label="Worker",
                task_name_prefix="worker",
            ),
            name="workspace-worker-supervisor",
        )

        # Wait for all workers or the supervisor (or until one fails)
        done, pending = await asyncio.wait(
            [*tasks, supervisor_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        # Check for exceptions
        for task in done:
            if task.exception():
                exception = task.exception()
                logger.error(f"Worker task failed: {exception}")
                # Cancel remaining tasks
                for p in pending:
                    p.cancel()
                if exception is not None:
                    raise exception

    except asyncio.CancelledError:
        logger.info("⚠️  Workers cancelled")
    except KeyboardInterrupt:
        logger.info("\n⚠️  Workers stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cancel all running tasks
        if supervisor_task and not supervisor_task.done():
            supervisor_task.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete cancellation
        if supervisor_task:
            await asyncio.gather(supervisor_task, return_exceptions=True)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Cleanup
        if bootstrap:
            logger.info("=" * 60)
            logger.info("🧹 Cleaning up resources...")
            logger.info("=" * 60)
            try:
                await bootstrap.cleanup()
            except Exception as e:
                logger.error(f"⚠️  Error during cleanup: {e}")

        try:
            from structure.registries.mcp_loader import close_all_clients

            await close_all_clients()
        except Exception as e:
            logger.debug(f"MCP client cleanup: {e}")

        logger.info("=" * 60)
        logger.info("✅ Agent Worker shutdown completed")
        logger.info("=" * 60)


async def run_workers_embedded(
    redis_client: Any, num_workers: int = 1, name_prefix: str = "embedded-worker"
) -> None:
    """Run event workers inside an existing process (no bootstrap).

    Called from the API lifespan when EMBED_WORKER=true so the worker shares
    the API's already-initialized redis client, DB engine pool, and imports.
    """
    shared_runtime = ExecutorInstanceManager()
    tasks: list[asyncio.Task] = []
    supervisor_task: asyncio.Task | None = None

    try:
        # Same fresh-deploy concern as run_workers: poll instead of sleeping
        # forever so the API lifespan can shut down cleanly on signal.
        workspace_ids = await _wait_for_workspaces(label="Embedded Worker")
        logger.info(f"[Embedded Worker] {len(workspace_ids)} active workspace(s)")

        started_keys: set[tuple[str, int]] = set()
        _start_missing_worker_tasks(
            redis_client=redis_client,
            tasks=tasks,
            started_keys=started_keys,
            workspace_ids=workspace_ids,
            num_workers_per_workspace=num_workers,
            name_prefix=name_prefix,
            shared_runtime=shared_runtime,
            task_name_prefix="embedded-worker",
        )

        supervisor_task = asyncio.create_task(
            _supervise_workspace_workers(
                redis_client=redis_client,
                tasks=tasks,
                started_keys=started_keys,
                num_workers_per_workspace=num_workers,
                name_prefix=name_prefix,
                shared_runtime=shared_runtime,
                label="Embedded Worker",
                task_name_prefix="embedded-worker",
            ),
            name="embedded-workspace-worker-supervisor",
        )

        done, pending = await asyncio.wait(
            [*tasks, supervisor_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for task in done:
            exc = task.exception()
            if exc:
                for p in pending:
                    p.cancel()
                raise exc

    except asyncio.CancelledError:
        if supervisor_task and not supervisor_task.done():
            supervisor_task.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        if supervisor_task:
            await asyncio.gather(supervisor_task, return_exceptions=True)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[Embedded Worker] Stopped")


@click.command()
@click.option(
    "-n",
    "--workers",
    default=1,
    show_default=True,
    type=int,
    help="Number of worker instances to run",
)
@click.option(
    "--name",
    default="worker",
    show_default=True,
    type=str,
    help="Worker name prefix for consumer identification",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable verbose logging (DEBUG level)",
)
def main(workers: int, name: str, verbose: bool) -> None:
    """
    Start Agent Worker(s) to process tasks from Redis stream.

    Examples:

        # Start single worker
        structure-worker

        # Start 4 workers for parallel processing
        structure-worker -n 4

        # Start workers with custom name prefix
        structure-worker -n 2 --name api-worker
    """
    if verbose:
        logging.getLogger("structure").setLevel(logging.DEBUG)

    if workers < 1:
        raise click.BadParameter("Number of workers must be at least 1")

    if workers > 32:
        raise click.BadParameter("Number of workers should not exceed 32")

    # Setup signal handlers
    def signal_handler(signum, frame):  # noqa: ARG001
        logger.info(f"Received signal {signum}, initiating shutdown...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run workers
    asyncio.run(run_workers(workers, name))


def main_sync():
    """同步包装函数，用于命令行入口点"""
    main()


if __name__ == "__main__":
    main()
