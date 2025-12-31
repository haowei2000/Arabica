#!/usr/bin/env python3
"""
Workers Module - 工作器模块

提供任务队列、生产者、消费者等工作器相关功能。

模块导出:
    - TaskQueueService: 任务队列服务（整合生产者和消费者）
    - TaskProducer: 任务生产者
    - TaskConsumer: 任务消费者
    - task_utils: 编码/解码工具函数
"""

from . import task_utils
from .task_consumer import TaskConsumer
from .task_producer import TaskProducer, get_task_producer
from .task_queue import TaskQueueService

__all__ = [
    "TaskConsumer",
    "TaskProducer",
    "TaskQueueService",
    "get_task_producer",
    "task_utils",
]
