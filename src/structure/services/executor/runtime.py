#!/usr/bin/env python3
"""
模块名称: runtime

功能描述:
    管理执行器实例的运行时容器，提供执行器的注册、获取和释放功能。
    用于在任务生命周期内维护执行器实例的映射关系。

作者: haowei
创建日期: 2025/12/26
最后修改: 2025/12/26 10:30
修改人员: haowei
版本: v1.0.0


依赖模块:
    - uuid
    - structure.interfaces.protocols.ExecutorProtocol

使用示例:
    runtime = ExecutorInstanceManager()
    runtime.attach(task_id, executor)
    executor = runtime.get(task_id)
"""

# structure/services/executor/runtime.py
from typing import Optional
from uuid import UUID

from structure.core.interfaces.protocols import ExecutorProtocol


class ExecutorInstanceManager:
    """Manages running executor instances lifecycle.

    This class maintains a mapping between task IDs and their associated
    executor instances during task execution. It handles attachment,
    retrieval, and cleanup of executor instances.

    Note: This is different from ExecutorTemplate (the ORM model) and
    ExecutorCRUD (database operations for templates).
    """

    def __init__(self):
        """初始化执行器实例存储字典"""
        self._instances: dict[UUID, ExecutorProtocol] = {}

    def attach(self, task_id: UUID, executor: ExecutorProtocol):
        """将执行器实例绑定到指定的任务ID

        Args:
            task_id (UUID): 任务唯一标识符
            executor (ExecutorProtocol): 执行器实例
        """
        self._instances[task_id] = executor

    def get(self, task_id: UUID) -> ExecutorProtocol | None:
        """根据任务ID获取对应的执行器实例

        Args:
            task_id (UUID): 任务唯一标识符

        Returns:
            Optional[ExecutorProtocol]: 执行器实例，如果不存在则返回None
        """
        return self._instances.get(task_id)

    def release(self, task_id: UUID):
        """释放指定任务ID对应的执行器实例

        Args:
            task_id (UUID): 任务唯一标识符
        """
        self._instances.pop(task_id, None)

    def exists(self, task_id: UUID) -> bool:
        """检查指定任务ID是否存在对应的执行器实例

        Args:
            task_id (UUID): 任务唯一标识符

        Returns:
            bool: 如果存在对应实例返回True，否则返回False
        """
        return task_id in self._instances
