# Agent Registry 实施状态报告

**日期**: 2025-12-23
**数据库**: aiwen_agent (根据 `.env` 配置)

## 当前状态 ✅

### 1. 数据库表结构

所有必要的表已在 `aiwen_agent` 数据库中创建:

```sql
-- ✅ agent_template 表
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

-- ✅ conversations 表
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    app_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(255) NOT NULL,
    -- ... 其他字段
);

-- ✅ messages 表 (含外键)
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    -- ... 其他字段
    CONSTRAINT fk_messages_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES conversations(id)
        ON DELETE CASCADE
);
```

### 2. 代码修复

#### ✅ Message 模型 - 添加外键约束

**文件**: `src/aiwen/models/agents/message.py`

```python
# 修复前
conversation_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)

# 修复后
conversation_id: Mapped[str] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("conversations.id", ondelete="CASCADE"),  # ← 添加外键
    nullable=False
)
```

#### ✅ DefaultAgentTemplate - 修正配置字段

**文件**: `src/aiwen/services/agents/agent_template/default/concrete.py`

```python
# 修复前
TEMPLATE = {
    "enable": True,  # ← 错误字段名
}

# 修复后
TEMPLATE = {
    "enabled": True,  # ← 正确字段名
}
```

#### ✅ Agent Registry - 完整实现

**文件**: `src/aiwen/services/agents/agent_registry.py`

- ✅ AgentRegistry 类（内存缓存 + 数据库持久化）
- ✅ register_default_templates() 函数
- ✅ init_agent_registry() 函数
- ✅ 完善的错误处理和日志

#### ✅ 应用启动集成

**文件**: `src/aiwen/core/lifespan.py`

```python
async def lifespan(app: FastAPI):
    # 启动时自动注册 agent templates
    await initialize_redis()
    await initialize_databases()
    await initialize_agent_registry()  # ← 新增

    yield

    # 关闭...
```

### 3. Alembic 迁移

**当前版本**: `da790b82e86b`

**已创建的迁移**:
- `dd983e0a2018_add_foreign_key_to_messages_.py` - 添加外键约束（如需要可应用）

## 测试结果

```bash
✓ Registered templates: ['DEFAULT001']
✓ Templates in database:
  - DEFAULT001: Default Detection Agent (enabled=True, version=1)
✓ Foreign key constraint: fk_messages_conversation exists
✓ Agent registry working correctly!
```

## 环境配置

当前使用的数据库配置（来自 `.env`）:

```bash
# PostgreSQL
POSTGRES__HOST=10.1.2.111
POSTGRES__PORT=5435
POSTGRES__USERNAME=postgres
POSTGRES__PASSWORD=difyai123456
POSTGRES__AIWEN_DBNAME=aiwen_agent  # ← 注意是 aiwen_agent

# MySQL
MYSQL__HOST=61.183.71.118
MYSQL__PORT=9220
MYSQL__USERNAME=root
MYSQL__PASSWORD=123456
MYSQL__DBNAME=unimax55_5509_710
```

## 使用方法

### 应用启动时自动注册

应用启动时会自动注册所有默认模板，无需手动操作:

```bash
# 启动应用
uvicorn aiwen.app:app --reload

# 日志输出
INFO:aiwen.core.lifespan:=== Application starting up ===
INFO:aiwen.services.agent.agent_registry:=== Registering default agent templates ===
INFO:aiwen.services.agent.agent_registry:✓ Registered new agent template: DEFAULT001
INFO:aiwen.services.agent.agent_registry:=== Agent template registration complete: 1 succeeded, 0 failed ===
INFO:aiwen.core.lifespan:Agent registry initialized successfully
```

### 在代码中使用

```python
from aiwen.services.executor.executor_registry import ExecutorRegistry

# 获取 Agent 类
template_cls = ExecutorRegistry.get("DEFAULT001")

# 创建实例
agent = template_cls(config={
   "model_provider": "ollama",
   "model_name": "qwen3:30b"
})

# 运行 Agent
result = await agent.run({"query": "Hello, world!"})
```

### 查询已注册的模板

```python
# 列出所有模板
templates = AgentRegistry.list()
print(templates)  # ['DEFAULT001']

# 检查模板是否存在
if AgentRegistry.is_registered("DEFAULT001"):
    print("Template exists!")
```

## 数据库迁移

如果需要修改表结构，使用 Alembic 迁移:

### 1. 创建新迁移

```bash
alembic revision --autogenerate -m "description of changes"
```

### 2. 检查生成的迁移

查看 `src/aiwen/migrations/versions/` 目录下的新文件。

### 3. 应用迁移

```bash
# 升级到最新版本
alembic upgrade head

# 或指定版本
alembic upgrade <revision_id>
```

### 4. 回滚迁移

```bash
# 回滚一个版本
alembic downgrade -1

# 或回滚到指定版本
alembic downgrade <revision_id>
```

### 5. 查看迁移历史

```bash
# 查看当前版本
alembic current

# 查看所有版本
alembic history

# 查看头版本
alembic heads
```

## 添加新的 Agent Template

### 步骤 1: 创建模板类

```python
# src/aiwen/services/agent/agent_template/my_agent/concrete.py
from aiwen.registries.base_class.base_executor import BaseAgentTemplate


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
      query = input_data.get("query", "")
      # ... 处理逻辑
      return {"answer": "response"}

   async def stream(self, input_data: dict):
      # 实现流式输出
      async for chunk in self.llm.astream(...):
         yield chunk
```

### 步骤 2: 注册到默认模板列表

编辑 `src/aiwen/services/agents/agent_registry.py`:

```python
async def register_default_templates(db_session: AsyncSession) -> None:
    from aiwen.plugins.executors.simple import DefaultAgentTemplate
    from aiwen.services.executor.executor_template.my_agent.concrete import MyCustomAgent  # 新增

    templates = [
        (DefaultAgentTemplate, DefaultAgentTemplate.TEMPLATE),
        (MyCustomAgent, MyCustomAgent.TEMPLATE),  # 新增
    ]

    # ... 注册逻辑保持不变
```

### 步骤 3: 重启应用

```bash
# 重启应用，新模板会自动注册
uvicorn aiwen.app:app --reload
```

## 故障排查

### 问题 1: 模板未注册

**症状**: `ValueError: Unknown agent template: 'XXX'`

**排查**:
1. 检查应用启动日志
2. 确认模板在 `register_default_templates()` 中
3. 检查数据库连接

```bash
# 查看日志
tail -f logs/app.log | grep agent_registry
```

### 问题 2: 数据库表不存在

**症状**: `relation "agent_template" does not exist`

**解决**:
```bash
# 检查 Alembic 版本
alembic current

# 应用所有迁移
alembic upgrade head
```

### 问题 3: 外键约束错误

**症状**: `Could not determine join condition between parent/child tables`

**解决**: 确保 Message 模型中有正确的外键定义:

```python
conversation_id: Mapped[str] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("conversations.id", ondelete="CASCADE"),
    nullable=False
)
```

## 性能监控

### 查看注册的模板数量

```python
from aiwen.services.executor.executor_registry import ExecutorRegistry

template_count = len(ExecutorRegistry.list())
print(f"Registered templates: {template_count}")
```

### 查询数据库中的模板

```sql
-- 查看所有模板
SELECT template_code, template_name, enabled, version, created_at
FROM agent_template
ORDER BY created_at DESC;

-- 统计启用的模板
SELECT COUNT(*) as enabled_count
FROM agent_template
WHERE enabled = true;

-- 查看最近注册的模板
SELECT template_code, template_name, created_at
FROM agent_template
ORDER BY created_at DESC
LIMIT 5;
```

## 未来改进

### 计划中的功能

1. **动态模板管理**
   - 支持通过 API 动态添加/更新模板
   - 模板版本管理

2. **模板继承**
   - 支持模板之间的继承关系
   - 减少重复配置

3. **模板分类**
   - 按功能分类管理模板
   - 标签系统

4. **性能优化**
   - 模板配置缓存
   - 懒加载机制

## 相关文档

- [Agent Registry 详细文档](./AGENT_REGISTRY.md)
- [架构分析](./ARCHITECTURE_ANALYSIS.md)
- [App 优化文档](./APP_OPTIMIZATION.md)

## 总结

✅ **完成项**:
- [x] Message 模型添加外键约束
- [x] DefaultAgentTemplate 配置修正
- [x] AgentRegistry 完整实现
- [x] 集成到应用启动流程
- [x] 数据库表创建和验证
- [x] 测试通过

✅ **数据库状态**:
- Database: `aiwen_agent`
- Tables: `agent_template`, `conversations`, `messages`
- Foreign Key: `fk_messages_conversation` ✓
- Alembic Version: `da790b82e86b`

✅ **应用状态**:
- Agent Registry 正常工作
- DEFAULT001 模板已注册
- 自动启动注册功能正常

系统已准备好用于生产环境！🎉
