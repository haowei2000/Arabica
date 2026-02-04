# Agent Worker 使用指南

## 概述

Agent Worker 是一个异步任务处理器，使用 `AgentRuntime` 管理 agent 实例的生命周期，支持流式输出和任务状态跟踪。

## 架构设计

### 核心组件

1. **AgentWorker** - 主要的 worker 类
   - 监听 Redis 任务队列
   - 使用 `AgentRuntime` 管理 agent 实例
   - 支持流式输出和事件发布

2. **AgentRuntime** - Agent 运行时管理器
   - 管理 agent 实例的生命周期
   - 支持通过 task_id 获取正在运行的 agent
   - 自动清理和资源释放

3. **AppAgentFactory** - Agent 工厂
   - 创建不同类型的 agent 实例
   - 支持动态注册新的 agent 类型

### 工作流程

```
1. 监听 Redis 队列 "agent_tasks"
   ↓
2. 接收任务消息 {task_id, app_id, payload}
   ↓
3. 从数据库获取 App 配置
   ↓
4. 创建 Agent 实例并附加到 Runtime
   ↓
5. 流式执行 Agent 并发布事件
   ↓
6. 收集结果并更新任务状态
   ↓
7. 释放 Agent 实例
```

## 功能特性

### 1. 运行时管理
- ✅ 使用 `AgentRuntime` 管理 agent 实例生命周期
- ✅ 自动清理和资源释放
- ✅ 支持查询正在运行的任务

### 2. 流式输出
- ✅ 支持流式执行 agent
- ✅ 实时发布 chunk 事件到 Redis
- ✅ 收集完整结果

### 3. 任务管理
- ✅ 任务状态跟踪 (pending → running → success/failed)
- ✅ 支持取消正在运行的任务
- ✅ 错误处理和重试机制

### 4. 事件系统
- ✅ chunk - 流式输出的每个片段
- ✅ success - 任务成功完成
- ✅ failed - 任务失败
- ✅ cancelled - 任务被取消

## 使用方法

### 方式 1: 使用启动脚本（推荐）

```bash
# 启动 worker（前台运行，推荐用于调试）
python src/aiwen/worker_cli.py

# 或使用模块方式
python -m aiwen.start_worker

# 后台运行（生产环境）
nohup python src/aiwen/worker_cli.py > logs/agent_worker.log 2>&1 &

# 使用 screen（推荐）
screen -S agent_worker
python src/aiwen/worker_cli.py
# 按 Ctrl+A 然后 D 离开

# 使用 tmux
tmux new -s agent_worker
python src/aiwen/worker_cli.py
# 按 Ctrl+B 然后 D 离开
```

**启动输出示例:**
```
============================================================
🚀 Agent Worker Startup
============================================================
🔧 Loading environment from: /path/to/project/src/.env
✅ Environment variables loaded
============================================================
Step 1: Initializing Redis client...
✅ Redis client initialized successfully
============================================================
🔧 Initializing Agent Registry...
============================================================
✅ Agent Registry initialized successfully!
📋 Registered templates: ['DEFAULT001']
✅ DEFAULT001 is registered and ready
============================================================
🚀 Starting Worker Loop...
============================================================
Starting AgentWorker...
```

### 方式 2: 在代码中使用

```python
import asyncio
from aiwen.celery_worker.task_worker import AgentWorker
from aiwen.extensions.database import get_session
from aiwen.middleware.cache_middleware import get_redis_client, init_redis_client


async def main():
    # 初始化 Redis
    await init_redis_client()
    redis_client = get_redis_client(is_async=True)

    # 创建 worker
    worker = AgentWorker(redis_client, get_session)

    # 启动 worker
    try:
        await worker.start()
    finally:
        await worker.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
```

### 方式 3: 与 FastAPI 集成

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    await init_redis_client()
    redis_client = get_redis_client(is_async=True)
    worker = AgentWorker(redis_client, get_session)

    # 在后台启动 worker
    worker_task = asyncio.create_task(worker.start())

    yield

    # 关闭时
    worker_task.cancel()
    await worker.cleanup()

app = FastAPI(lifespan=lifespan)
```

## API 参考

### AgentWorker 类

#### 初始化
```python
worker = AgentWorker(redis_client, db_session_factory)
```

#### 方法

**start()**
启动 worker，开始监听任务队列。

```python
await worker.start()
```

**process_task(message_data)**
处理单个任务（通常由 start() 自动调用）。

```python
await worker.process_task(message_data)
```

**publish_event(task_id, event_data)**
发布任务事件到 Redis。

```python
await worker.publish_event(task_id, {"event": "chunk", "data": "..."})
```

**get_running_agent(task_id)**
获取正在运行的 agent 实例。

```python
agent = worker.get_running_agent(task_id)
```

**get_running_task_count()**
获取正在运行的任务数量。

```python
count = worker.get_running_task_count()
```

**cancel_task(task_id)**
取消正在运行的任务。

```python
success = await worker.cancel_task(task_id)
```

**cleanup()**
清理所有资源。

```python
await worker.cleanup()
```

## 配置要求

### 重要提示

**Agent Worker 是独立进程**：Worker 不会执行 FastAPI 的 lifespan 钩子，因此需要在 Worker 启动时独立初始化 AgentRegistry。

启动脚本 `run_agent_worker.py` 已经包含了这个初始化：
```python
# 初始化 Agent Registry
await init_agent_registry()
```

### 环境变量

确保以下环境变量已配置：

```bash
# Redis 配置
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# 数据库配置
POSTGRES__HOST=localhost
POSTGRES__PORT=5432
POSTGRES__USERNAME=postgres
POSTGRES__PASSWORD=your_password
POSTGRES__AIWEN_DBNAME=aiwen
```

### 依赖项

```bash
pip install redis asyncio sqlalchemy
```

## 监控和调试

### 日志

Worker 会输出详细的日志信息：

```
2025-12-23 19:30:00 - aiwen.workers.agent_worker - INFO - Starting AgentWorker...
2025-12-23 19:30:01 - aiwen.workers.agent_worker - INFO - Processing task xxx for app yyy
2025-12-23 19:30:02 - aiwen.workers.agent_worker - INFO - Agent instance created for task xxx, type: chat
2025-12-23 19:30:05 - aiwen.workers.agent_worker - INFO - Task xxx completed successfully with 15 chunks
```

### 查看运行状态

```python
from aiwen.celery_worker.task_worker import AgentWorker

# 获取正在运行的任务数
count = worker.get_running_task_count()
print(f"Running tasks: {count}")

# 获取特定任务的 agent
agent = worker.get_running_agent(task_id)
if agent:
    print(f"Agent config: {agent.config}")
```

## 故障排除

### 常见问题

**Q: Worker 无法连接到 Redis**
```
A: 检查 Redis 是否正在运行，端口是否正确
   redis-cli ping  # 应该返回 PONG
```

**Q: 任务一直处于 pending 状态**
```
A: 确保 worker 正在运行
   检查 Redis 队列: redis-cli SUBSCRIBE agent_tasks
```

**Q: Agent 类型未注册错误**
```
A: 确保在 AppAgentFactory 中注册了对应的 agent 类型
   检查 src/aiwen/services/agents/app_factory.py
```

**Q: 数据库连接错误**
```
A: 检查数据库配置和连接
   确保数据库迁移已执行: alembic upgrade head
```

## 性能优化

### 建议

1. **并发数控制** - 根据服务器资源调整 worker 数量
2. **连接池配置** - 优化数据库和 Redis 连接池大小
3. **内存管理** - 及时释放完成的 agent 实例
4. **日志级别** - 生产环境使用 WARNING 或 ERROR 级别

### 示例配置

```python
# database.py
DEFAULT_POOL_CONFIG = {
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 30,
}

# cache_middleware.py
REDIS_CONFIG = {
    "max_connections": 50,
    "decode_responses": False,
}
```

## 扩展开发

### 添加新的 Agent 类型

1. 创建新的 agent 类继承 `BaseAgentTemplate`
2. 在 `AppAgentFactory` 中注册
3. 在数据库中添加对应的 agent_template

```python
# 1. 定义新的 agent
class MyCustomAgent(BaseAgentTemplate):
    async def run(self, input_data):
        # 实现逻辑
        return {"result": "..."}

    async def stream(self, input_data):
        # 流式输出
        for chunk in generate_chunks():
            yield chunk

# 2. 注册到工厂
AppAgentFactory.register("my_custom_agent", MyCustomAgent)
```

### 添加新的事件类型

在 `publish_event` 调用时使用新的事件类型：

```python
await self.publish_event(task_id, {
    "event": "progress",
    "data": {"percent": 50, "input": "Half done"}
})
```

## 生产部署

### 使用 systemd

创建 `/etc/systemd/system/agent-worker.service`:

```ini
[Unit]
Description=Agent Worker Service
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/ai630
ExecStart=/path/to/python3 src/scripts/run_agent_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable agent-worker
sudo systemctl start agent-worker
sudo systemctl status agent-worker
```

### 使用 Docker

```dockerfile
FROM python:3.12

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

CMD ["python", "src/scripts/run_agent_worker.py"]
```

### 使用 Supervisor

```ini
[program:agent-worker]
command=/path/to/python3 src/scripts/run_agent_worker.py
directory=/path/to/ai630
autostart=true
autorestart=true
stderr_logfile=/var/log/agent-worker.err.log
stdout_logfile=/var/log/agent-worker.out.log
```

## 更新日志

### v1.1.0 (2025-12-23)
- ✨ 集成 AgentRuntime 管理 agent 实例生命周期
- ✨ 支持流式输出和实时事件发布
- ✨ 添加任务取消功能
- ✨ 改进错误处理和日志记录
- 🐛 修复 JSON 序列化问题
- 🐛 修复 UUID 处理问题

### v1.0.0 (2025-12-23)
- 🎉 初始版本发布
