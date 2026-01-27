#!/usr/bin/env python3
"""
模块名称: {模块名称}

功能描述:
    {详细描述模块的主要功能、实现逻辑和用途}

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/23 14:11
修改人员: haowei
版本: {版本号，如 v1.0.0}


依赖模块:
    - {依赖模块1}
    - {依赖模块2}

使用示例:
    {提供简单的使用示例代码}
"""

# aiwen/services/agent/runtime.py
from uuid import UUID

from aiwen.services.agent.base import BaseAgentTemplate


class AgentRuntime:
    def __init__(self):
        self._instances: dict[UUID, BaseAgentTemplate] = {}

    def attach(self, task_id: UUID, agent: BaseAgentTemplate):
        self._instances[task_id] = agent

    def get(self, task_id: UUID) -> BaseAgentTemplate:
        return self._instances[task_id]

    def release(self, task_id: UUID):
        self._instances.pop(task_id, None)
