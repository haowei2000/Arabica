#!/usr/bin/env python3
"""
模块名称: Redis配置模块

功能描述:
    该模块定义了Redis的配置模型，用于从环境变量加载Redis连接参数。
    支持通过环境变量设置主机地址、端口、用户名、密码和数据库编号。

作者: haowei
创建日期: 2025/11/30
最后修改: 2025/11/30 16:06
修改人员: haowei
版本: v1.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - pydantic

使用示例:
    redis_config = RedisConfig()
    print(redis_config.host)
"""

from pydantic import BaseModel, Field, PositiveInt


class RedisConfig(BaseModel):
    """Redis sub-configuration. Uses environment variable aliases
    (e.g. REDIS__HOST) so values can be provided from a project .env file.
    """

    host: str = Field(default="127.0.0.1", description="Redis host")
    port: PositiveInt = Field(6379, description="Redis port")
    username: str = Field(default="", description="Redis user")
    password: str = Field(default="", description="Redis password")
    db: PositiveInt = Field(default=0, description="Redis database number")

    # Stream / queue name configuration
    workspace_label: str = Field(
        default="workspace", description="Redis key prefix for workspace streams"
    )
    run_label: str = Field(
        default="run", description="Redis key prefix for run streams"
    )
    executor_label: str = Field(
        default="executor", description="Redis stream name for executor task queue"
    )
    stream_events_suffix: str = Field(
        default="events", description="Suffix for event streams"
    )
    consumer_group: str = Field(
        default="run_workers", description="Consumer group name for worker stream"
    )
    run_resume_approval_suffix: str = Field(
        default="resume_approval",
        description="Redis key suffix for run resume approval",
    )
    worker_max_concurrent_events: PositiveInt = Field(
        default=32,
        description="Maximum number of workspace stream events processed concurrently per worker",
    )

    # SSE event type names
    event_type_keepalive: str = Field(
        default="keepalive", description="SSE keepalive event type"
    )
    event_type_error: str = Field(default="error", description="SSE error event type")
    event_type_disconnect: str = Field(
        default="disconnect", description="SSE disconnect event type"
    )
