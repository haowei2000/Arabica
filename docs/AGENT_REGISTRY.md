# Agent Registry - Agent Template 注册系统

## 概述

Agent Registry 是一个用于管理 Agent 模板的注册系统，提供内存缓存和数据库持久化功能。该系统在应用启动时自动注册默认的 Agent 模板，确保它们在运行时可用。

## 架构设计

```
┌─────────────────────────────────────────────┐
│          Application Startup                │
│         (core/lifespan.py)                  │
└──────────────────┬──────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────┐
│   Initialize Agent Registry                 │
│   (init_agent_registry)                     │
└──────────────────┬──────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────┐
│   Register Default Templates                │
│   (register_default_templates)              │
└──────────────────┬──────────────────────────┘
                   │
           ┌───────┴───────┐
           ↓               ↓
    ┌──────────┐    ┌──────────────┐
    │  Memory  │    │   Database   │
    │  Cache   │    │ Persistence  │
    │ Registry │    │ (PostgreSQL) │
    └──────────┘    └──────────────┘
```

## 核心组件

### 1. AgentRegistry 类

位置: `src/aiwen/services/agents/agent_registry.py`

**职责**:
- 内存中维护 Agent 模板类的注册表
- 将模板信息持久化到数据库
- 提供模板查询和管理功能

**主要方法**:

```python
class AgentRegistry:
    @classmethod
    async def register(
        template_code: str,
        template_name: str,
        agent_cls: Type[BaseAgentTemplate],
        db_session: AsyncSession,
        config: dict = None,
        enabled: bool = True,
        version: int = 1
    ) -> AgentTemplate:
        """注册 Agent 模板（内存 + 数据库）"""

    @classmethod
    def get(template_code: str) -> Type[BaseAgentTemplate]:
        """根据模板代码获取 Agent 类"""

    @classmethod
    def list() -> List[str]:
        """列出所有已注册的模板代码"""

    @classmethod
    def is_registered(template_code: str) -> bool:
        """检查模板是否已注册"""
```

### 2. Default Agent Template

位置: `src/aiwen/services/agents/agent_template/default/concrete.py`

**配置**:
```python
TEMPLATE = {
    "template_code": "DEFAULT001",
    "template_name": "Default Detection Agent",
    "enabled": True,
    "version": 1,
    "config": {
        "model_provider": "ollama",
        "model_name": "qwen3:30b"
    }
}
```

### 3. 数据库模型

位置: `src/aiwen/models/agents/agent_template.py`

**表结构**:
```sql
CREATE TABLE agent_template (
    id UUID PRIMARY KEY,
    template_code VARCHAR UNIQUE NOT NULL,
    template_name VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    config JSONB,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);
```

## 使用方法

### 1. 自动注册（应用启动时）

应用启动时会自动注册默认模板，无需手动操作:

```python
# 在 core/lifespan.py 中
async def lifespan(app: FastAPI):
    # 应用启动
    await initialize_redis()
    await initialize_databases()
    await initialize_agent_registry()  # ← 自动注册默认模板

    yield

    # 应用关闭
    # ...
```

### 2. 获取已注册的模板

```python
from aiwen.services.executor.executor_registry import ExecutorRegistry

# 获取模板类
template_cls = ExecutorRegistry.get("DEFAULT001")

# 实例化 Agent
agent = template_cls(config={"model_name": "qwen3:30b"})

# 运行 Agent
result = await agent.run({"query": "Hello, world!"})
```

### 3. 列出所有模板

```python
# 列出所有已注册的模板
templates = AgentRegistry.list()
print(f"Registered templates: {templates}")
# Output: ['DEFAULT001']
```

### 4. 检查模板是否存在

```python
if AgentRegistry.is_registered("DEFAULT001"):
    print("Template exists!")
```

## 添加新的默认模板

### 步骤 1: 创建模板类

```python
# src/aiwen/services/agent/agent_template/my_agent/concrete.py
from aiwen.services.executor.base import BaseAgentTemplate


class MyCustomAgent(BaseAgentTemplate):
   """自定义 Agent 模板"""

   TEMPLATE = {
      "template_code": "CUSTOM001",
      "template_name": "My Custom Agent",
      "enabled": True,
      "version": 1,
      "config": {
         "model_provider": "openai",
         "model_name": "gpt-4"
      }
   }

   async def run(self, input_data: dict):
      # 实现 Agent 逻辑
      return {"result": "success"}
```

### 步骤 2: 注册到默认模板列表

在 `agent_registry.py` 的 `register_default_templates` 函数中添加:

```python
async def register_default_templates(db_session: AsyncSession) -> None:
   from aiwen.services.executor.executor_template.default.concrete import DefaultAgentTemplate
   from aiwen.services.executor.executor_template.my_agent.concrete import MyCustomAgent  # 新增

   templates = [
      (DefaultAgentTemplate, DefaultAgentTemplate.TEMPLATE),
      (MyCustomAgent, MyCustomAgent.TEMPLATE),  # 新增
   ]

   # ... 注册逻辑
```

### 步骤 3: 重启应用

应用重启后，新模板会自动注册。

## 错误处理

### 1. 模板注册失败

系统会捕获并记录错误，但不会阻止应用启动:

```python
ERROR:aiwen.services.agents.agent_registry:Failed to register template CUSTOM001: ...
```

### 2. 模板不存在

尝试获取不存在的模板时会抛出异常:

```python
try:
    template = AgentRegistry.get("NONEXISTENT")
except ValueError as e:
    print(e)
    # Output: Unknown agent template: 'NONEXISTENT'. Available templates: DEFAULT001
```

## 日志示例

成功注册时的日志:

```
INFO:aiwen.services.agents.agent_registry:=== Registering default agent templates ===
INFO:aiwen.services.agents.agent_registry:✓ Registered new agent template: DEFAULT001 (id: ...)
INFO:aiwen.services.agents.agent_registry:=== Agent template registration complete: 1 succeeded, 0 failed ===
INFO:aiwen.core.lifespan:Agent registry initialized successfully
```

## 数据库查询

### 查看已注册的模板

```python
from aiwen.extensions.database import get_session
from aiwen.services.crud.agent_template_crud import AgentTemplateCRUD

async with get_session("aiwen") as session:
    crud = AgentTemplateCRUD(session)
    templates = await crud.list_templates()
    for template in templates:
        print(f"{template.template_code}: {template.template_name}")
```

### SQL 查询

```sql
-- 查看所有模板
SELECT template_code, template_name, enabled, version, created_at
FROM agent_template;

-- 查看特定模板
SELECT * FROM agent_template WHERE template_code = 'DEFAULT001';

-- 统计启用的模板数量
SELECT COUNT(*) FROM agent_template WHERE enabled = true;
```

## 测试

### 单元测试示例

```python
import asyncio
from aiwen.services.executor.executor_registry import ExecutorRegistry, init_executor_registry


async def test_agent_registry():
   # 初始化注册表
   await init_executor_registry()

   # 测试内存注册
   assert ExecutorRegistry.is_registered("DEFAULT001")
   assert "DEFAULT001" in ExecutorRegistry.list()

   # 测试获取模板
   template_cls = ExecutorRegistry.get("DEFAULT001")
   assert template_cls is not None

   # 测试数据库持久化
   from aiwen.extensions.database import get_session
   from aiwen.services.crud.agent_template_crud import AgentTemplateCRUD

   async with get_session("aiwen") as session:
      crud = AgentTemplateCRUD(session)
      template = await crud.get_template_by_code("DEFAULT001")
      assert template is not None
      assert template.template_name == "Default Detection Agent"

   print("✓ All tests passed!")


if __name__ == "__main__":
   asyncio.run(test_agent_registry())
```

## 性能考虑

### 内存注册优先

- **首次访问**: 从内存缓存获取 Agent 类（O(1) 时间复杂度）
- **数据库持久化**: 仅用于应用重启后的数据恢复
- **无需每次查询数据库**: 提高运行时性能

### 双重注册的优势

1. **内存缓存**: 快速访问 Agent 类
2. **数据库持久化**:
   - 记录模板注册历史
   - 支持动态更新配置
   - 跨应用实例共享模板信息

## 最佳实践

### 1. 模板命名规范

```python
# 推荐格式: <PURPOSE><NUMBER>
"DEFAULT001"  # 默认模板
"NL2SQL001"   # NL2SQL 模板
"CUSTOM001"   # 自定义模板
```

### 2. 配置管理

将复杂配置存储在模板的 `config` 字段中:

```python
TEMPLATE = {
    "template_code": "ADVANCED001",
    "template_name": "Advanced Agent",
    "config": {
        "model_provider": "openai",
        "model_name": "gpt-4",
        "temperature": 0.7,
        "max_tokens": 2000,
        "system_prompt": "You are a helpful assistant."
    }
}
```

### 3. 版本管理

使用版本号追踪模板变更:

```python
TEMPLATE = {
    "template_code": "DEFAULT001",
    "version": 2,  # 升级版本
    "config": {
        # 更新的配置
    }
}
```

## 故障排查

### 问题 1: 模板未注册

**症状**: `ValueError: Unknown agent template`

**解决**:
1. 检查模板是否在 `register_default_templates` 中
2. 查看应用启动日志是否有错误
3. 确认数据库连接正常

### 问题 2: 数据库表不存在

**症状**: `relation "agent_template" does not exist`

**解决**:
```sql
-- 手动创建表
CREATE TABLE agent_template (
    id UUID PRIMARY KEY,
    template_code VARCHAR UNIQUE NOT NULL,
    template_name VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    config JSONB,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);
```

### 问题 3: 重复注册

**症状**: 日志显示 "Agent template already exists"

**说明**: 这是正常行为，系统会跳过已存在的模板。

## 未来扩展

### 1. 动态注册

支持运行时动态注册新模板:

```python
await AgentRegistry.register(
    template_code="DYNAMIC001",
    template_name="Dynamic Agent",
    agent_cls=DynamicAgent,
    db_session=session
)
```

### 2. 模板版本控制

支持同一模板的多个版本并存:

```python
AgentRegistry.get("DEFAULT001", version=2)
```

### 3. 模板继承

支持模板之间的继承关系:

```python
class AdvancedAgent(DefaultAgentTemplate):
    TEMPLATE = {
        "template_code": "ADVANCED001",
        "parent_template": "DEFAULT001",
        # ...
    }
```

## 总结

Agent Registry 系统提供了一个强大而灵活的模板管理机制:

✅ **自动化**: 应用启动时自动注册
✅ **高性能**: 内存缓存快速访问
✅ **可靠性**: 数据库持久化
✅ **可扩展**: 易于添加新模板
✅ **容错性**: 注册失败不影响应用启动

通过这个系统，可以轻松管理和使用各种 Agent 模板，为构建复杂的 AI 应用提供坚实的基础。
