"""Worker-process lifecycle helpers shared by all knowledge/context-sync tasks.

Worker signals
--------------
worker_process_init    — create a persistent event loop + register DB engines
worker_process_shutdown — dispose DB connections + close the event loop

run_async()
-----------
Run an async coroutine on the worker-process-level event loop.
All Celery tasks that need async DB access call this helper instead of
creating a new loop per task.
"""

import asyncio
import logging

from celery.signals import worker_process_init, worker_process_shutdown

logger = logging.getLogger(__name__)

# Worker-level event loop — one per worker process
_worker_loop: asyncio.AbstractEventLoop | None = None
_db_initialized: bool = False


@worker_process_init.connect
def _init_worker_process(**_):
    global _worker_loop, _db_initialized

    _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)

    from structure.extensions.database import _ensure_registered
    _ensure_registered()
    _db_initialized = True

    logger.info("Worker process initialized: event loop and DB connections ready")


@worker_process_shutdown.connect
def _shutdown_worker_process(**_):
    global _worker_loop, _db_initialized

    if _worker_loop is not None:
        async def _cleanup():
            from structure.extensions.database import dispose_all
            await dispose_all()

        try:
            _worker_loop.run_until_complete(_cleanup())
        except Exception as exc:
            logger.warning(f"Error during worker shutdown cleanup: {exc}")

        _worker_loop.close()
        _worker_loop = None

    _db_initialized = False
    logger.info("Worker process shutdown: DB connections disposed")


def run_async(coro):
    """Run an async coroutine on the worker's persistent event loop."""
    global _worker_loop, _db_initialized

    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)

    if not _db_initialized:
        from structure.extensions.database import _ensure_registered
        _ensure_registered()
        _db_initialized = True

    return _worker_loop.run_until_complete(coro)
