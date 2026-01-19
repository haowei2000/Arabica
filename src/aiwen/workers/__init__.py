#!/usr/bin/env python3
"""
Workers Module - Celery-based task processing

Provides Celery task queue integration for async agent processing.

Module exports:
    - TaskProducer: Task dispatcher (uses Celery)
    - TaskConsumer: Event consumer (reads from Redis Streams)
    - process_agent_task: Celery task for agent processing
    - task_utils: Encoding/decoding utilities
"""

from . import task_utils
from .task_consumer import TaskConsumer
from .task_producer import TaskProducer, get_task_producer
from .tasks import process_agent_task

__all__ = [
    "TaskConsumer",
    "TaskProducer",
    "get_task_producer",
    "process_agent_task",
    "task_utils",
]
