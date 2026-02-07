#!/usr/bin/env python3
"""
模块名称: {模块名称}

功能描述:
    {详细描述模块的主要功能、实现逻辑和用途}

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/23 14:10
修改人员: haowei
版本: {版本号，如 v1.0.0}

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - {依赖模块1}
    - {依赖模块2}

使用示例:
    {提供简单的使用示例代码}
"""

# aiwen/services/agent/app_factory.py
from typing import Any, Dict

from aiwen.services.agent.agent_registry import AgentRegistry
from aiwen.services.agent.base import Executor


class AppAgentFactory:
    """
    应用代理工厂类，用于为单个应用创建代理实例。

    每个应用都有一个特定的代理模板，该工厂负责根据应用配置和任务载荷创建相应的代理实例。
    """

    def __init__(
        self,
        appid: str,
        template_code: str,
        app_config: dict[str, Any],
    ):
        """
        初始化应用代理工厂

        Args:
            appid (str): 应用的唯一标识符
            template_code (str): Agent 模板代码（在 AgentRegistry 中注册的代码）
            app_config (Dict[str, Any]): 应用的默认配置，将与任务载荷合并
        """
        self.appid = appid
        self.template_code = template_code
        self.app_config = app_config

        # 启动时校验一次，失败即是配置错误
        self._agent_cls = AgentRegistry.get(template_code)

    def create(self, payload: dict[str, Any]) -> Executor:
        """
        为单个任务创建代理实例

        该方法将应用ID、应用配置和任务载荷合并为最终配置，然后创建代理实例

        Args:
            payload (Dict[str, Any]): 任务特定的配置参数，将与应用默认配置合并

        Returns:
            Executor: 创建的代理实例
        """
        config = {
            "appid": self.appid,
            **self.app_config,
            **payload,
        }

        return self._agent_cls(config)
