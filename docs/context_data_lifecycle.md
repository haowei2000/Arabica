# Context Data Lifecycle

> 系统中 Skill、Knowledge、Tool、Memory/Context、Trigger 的完整数据生命周期文档。

---

## 作用域层级总览

```
Global (User Level)
├── Skill          - 用户级技能模板
├── Knowledge      - 知识库（含 Document + Chunk + Embeddings）
├── Tool           - 工具（系统内置 inner + 用户自定义 external）
└── Context        - 全局上下文（含向量嵌入）

Workspace Level
├── WorkspaceContext  - 工作区运行时上下文（DB + 内存缓存）
├── WorkspaceTrigger  - 事件驱动的自动化触发器
└── ToolBundle        - 工具分组
```

---

## 1. Skill 生命周期

**相关文件：**
- Model: `src/structure/models/context/skill.py`
- CRUD: `src/structure/services/context/skill_crud.py`
- Router: `src/structure/routers/context/skills.py`

**字段：** `id, user_id, name, description, content, glance, summary, path, source_id, tags, meta, files, created_at, updated_at`

```
[API POST /context/skills]
        │
        ▼
SkillCRUD.create()                  ← PostgreSQL: skill 表
        │
        ▼
SkillProcessor.process_skill()      ← 解析 Markdown，提取 glance/summary
        │
        ▼
sync_skill_to_contexts.delay()      ← Celery 异步任务
        │
        ▼
ContextCRUD.create(type=SKILL)      ← 同步到 context 表（含向量嵌入）
```

| 操作 | 说明 |
|------|------|
| 创建 | `POST /context/skills`，自动同步 context 副本 |
| 读取 | `GET /context/skills/{id}`，直接 DB 查询 |
| 更新 | `PUT /context/skills/{id}`，重新生成 glance |
| 删除 | 硬删除，同步清理 context 副本 |

- **作用域：** User 级别（无 workspace 绑定）

---

## 2. Knowledge 生命周期

**相关文件：**
- Model: `src/structure/models/context/knowledge/knowledge.py`
- CRUD: `src/structure/services/context/knowledge/knowledge_crud.py`
- Document CRUD: `src/structure/services/context/knowledge/document_crud.py`
- Chunker: `src/structure/services/context/knowledge/chunker.py`
- Embeddings: `src/structure/services/context/knowledge/embeddings.py`

**实体关系：** `Knowledge` → `Document` → `Chunk`（层级关系）

```
[API 创建知识库]
        │
        ▼
KnowledgeCRUD.create()              ← PostgreSQL: knowledge 表

[用户上传文档]
        │
        ├──→ S3 存储 (object_key)
        │
        ▼
DocumentCRUD.create()               ← PostgreSQL: document 表 (status=pending)
        │
        ▼
文档处理管线 (Celery)
   ├── 从 S3 读取内容
   ├── 分块 (Chunker)
   └── 生成向量嵌入 (384/768/1024/1536 维)
        │
        ▼
ChunkCRUD.create()                  ← PostgreSQL: chunk 表 + pgvector HNSW 索引
        │
        ▼
Knowledge 计数更新 (document_count, chunk_count)
```

**检索路径：**
```
用户查询
  → 生成 Query Embedding
  → cosine_search() (pgvector <=> 运算符)
  → 返回 (Chunk, similarity_score) 列表
```

**向量维度支持：** `embedding_384` / `embedding_768` / `embedding_1024` / `embedding_1536`（兼容不同 Embedding 模型）

| 操作 | 说明 |
|------|------|
| 创建 | `POST /context/knowledge/create` |
| 文档上传 | `POST /context/knowledge/{id}/documents` |
| 语义检索 | `POST /context/knowledge/{id}/search` |
| 删除文档 | Document 支持 `is_deleted` 软删除 |

- **作用域：** User 级别

---

## 3. Tool 生命周期

**相关文件：**
- Model: `src/structure/models/context/tools/tool.py`
- CRUD: `src/structure/services/context/tools/tool_crud.py`
- Bundle CRUD: `src/structure/services/context/tools/tool_bundle_crud.py`
- Inner tools: `src/structure/plugins/tools/`

### 3a. Inner Tool（系统内置）

```
代码定义 (@register_tool 装饰器)
        │
        ▼
应用启动 → ToolRegistry 注册（内存）
        │
        ▼
ToolRegistry.sync()
   ├── Phase 1-3: PostgreSQL tool 表 upsert (tool_type='inner', user_id=NULL)
   ├── Phase 4:   ToolBundle 按 category 分组
   └── Phase 5:   Context 表 upsert（每个 user × 每个 inner tool）
                  └── dispatch sync_inner_tool_to_contexts.delay(tool_id)
                            │
                            ▼
                    WorkspaceContext 写入 tools/{name}（所有活跃工作区）
                    + 生成 embedding 写入 Context 表
```

### 3b. External Tool（用户自定义）

```
[API POST /context/tools]
        │
        ├── 校验：名称不与 InnerTool 冲突
        ├── 校验：chain 步骤所引用的工具必须存在
        │
        ▼
ToolCRUD.create_tool()              ← PostgreSQL: tool 表 (tool_type='external')
   ├── inner_tool_name: 单工具委托模式
   └── chain: 管道执行模式（多步骤）
```

**执行路径（Agent 运行时）：**
```
Agent 调用工具
  → ToolCaller 查找 Tool 记录
      ├── inner:    直接从 ToolRegistry 执行
      └── external: 按 parameter_mapping / chain 委托执行
```

### 3c. ToolBundle

```
[API POST /context/tools/bundles]
        │
        ▼
ToolBundleCRUD.create()             ← PostgreSQL: tool_bundle + tool_bundle_item 表
   └── 校验：所有 tool_id 必须可访问（owner / public / inner）

执行时：将 Bundle 传给 Executor，限定 Agent 可用工具集
```

| 字段 | 说明 |
|------|------|
| `tool_type` | `inner`（系统）/ `external`（用户） |
| `bundle_type` | `inner` / `mcp` / `user` |
| `user_id` | inner tool 为 NULL，external tool 为用户 UUID |

---

## 4. Context / Memory 生命周期

系统存在**两种** Context，各司其职：

### 4a. Global Context（全局持久化 + 向量检索）

**相关文件：**
- Model: `src/structure/models/context/context.py`
- CRUD: `src/structure/services/context/context_crud.py`

**Context Type 枚举：**
`CHUNK` / `CONVERSATION` / `MESSAGE` / `SHORT_MEMORY` / `SKILL` / `TOOL` / `KNOWLEDGE`

```
来源：
  ├── Skill 同步任务        → type=SKILL
  ├── Knowledge/Chunk 同步  → type=KNOWLEDGE / CHUNK
  ├── 对话记录             → type=CONVERSATION / MESSAGE
  └── 短期记忆             → type=SHORT_MEMORY

存储：PostgreSQL context 表
  ├── path: 路径寻址
  ├── glance / summary / content: 渐进式披露（三层）
  ├── embedding_384/768/1024/1536: pgvector 向量索引
  └── importance / tags / keywords: 辅助检索字段
```

**检索方式：**

| 方式 | 接口 | 说明 |
|------|------|------|
| 向量检索 | `cosine_search()` | 基于 embedding 相似度 |
| 文本检索 | `grep()` | 正则/关键词匹配 |
| 混合检索 | `hybrid_search()` | 向量 + 文本加权融合 |

### 4b. WorkspaceContext（工作区运行时内存）

**相关文件：**
- Model: `src/structure/models/context/workspace_context.py`
- Service: `src/structure/services/workspace_context/workspace_context_service.py`
- Framework: `src/structure/frameworks/context/layer.py`（ContextStore）

**双层存储架构：**
```
DB Layer:      WorkspaceContext 表（持久化）
Cache Layer:   ContextStore（内存 Trie 树，快速查询）
Service Layer: WorkspaceContextService（桥接两层）
```

**加载流程：**
```
工作区初始化
  → WorkspaceContextService.load()
      ├── 从 DB 查询所有非删除 WorkspaceContext 记录
      ├── 注册 schema 节点（tools / skills / knowledge / long_memory）
      └── 写入 ContextStore 内存
```

**写入流程：**
```
service.set(path, glance, overview, detail, tags, meta)
  → 更新 ContextStore 内存
  → _sync_to_db()
      └── CREATE or UPDATE WorkspaceContext 记录
```

**删除流程：**
```
service.delete(path, recursive=False/True)
  → 从 ContextStore 删除
  → DB: is_deleted = True（软删除）
```

**ContextStore 查询 API：**

| 方法 | 说明 |
|------|------|
| `get(path, level)` | 按路径获取，支持 GLANCE/OVERVIEW/DETAIL 三级 |
| `glob(pattern)` | 通配符匹配（`*` 单级，`**` 任意深度） |
| `children(prefix)` | 直接子节点 |
| `descendants(prefix)` | 所有后代节点 |
| `tree(root, level)` | 层级树结构 |
| `glance(prefix)` | 快速扫描摘要 |

**渐进式披露（三层）：**

| 层级 | 字段 | DetailLevel | 用途 |
|------|------|-------------|------|
| 1 | `glance` | GLANCE (1) | 一行摘要，快速扫描 |
| 2 | `summary` | OVERVIEW (2) | 结构化概要 |
| 3 | `content` | DETAIL (3) | 完整内容 |

**Agent 通过 context_tools 操作 WorkspaceContext：**

| 工具 | 操作 |
|------|------|
| `create_context` | 写入新节点 |
| `read_context` | 读取指定路径 |
| `update_context` | 更新内容 |
| `delete_context` | 软删除 |
| `glob_context` | 通配符路径查询 |
| `tree_context` | 层级树结构 |
| `search_context` | 语义/关键词检索 |
| `glance_context` | 快速摘要扫描 |
| `list_context` | 列举子节点 |

---

## 5. Trigger 生命周期

**相关文件：**
- Model: `src/structure/models/workspaces/workspace_trigger.py`
- Processor: `src/structure/services/triggers/trigger_processor.py`

**字段：** `id, workspace_id, user_id, name, event_type, condition_type, condition_value, condition_field, tool_name, action_params, priority, enabled`

```
[API 创建 Trigger]
        │
        ▼
WorkspaceTrigger 存入 DB
   ├── event_type:      监听哪类事件（如 user.message）
   ├── condition_type:  触发条件类型
   ├── condition_value: 匹配规则
   └── tool_name + action_params: 命中后执行的工具

[事件发布（如 user.message）]
        │
        ▼
TriggerProcessor
   ├── 从 DB 加载匹配触发器（workspace_id + event_type + enabled=true）
   ├── TriggerConditionEvaluator.evaluate(payload)
   └── 命中 → 调用 ToolRegistry 执行 tool_name（自动注入 workspace_id）
                    │
                    ▼
            结果写入 event payload._trigger_context
```

**Condition Type 枚举：**

| 类型 | 说明 |
|------|------|
| `always` | 始终触发 |
| `keyword` | 在 `condition_field` 中子串匹配 |
| `regex` | 在 `condition_field` 中正则匹配 |
| `jsonpath` | JSONPath 表达式求值 |
| `first_run` | 仅在工作区第一次 Run 时触发 |

- **作用域：** Workspace 级别（`workspace_id=NULL` 时为用户级模板）

---

## 全局数据流图

```
外部请求 / 用户消息
        │
        ▼
    EventPublisher
   ├── 写入 PostgreSQL: events 表
   └── 写入 Redis Stream
        │
        ├──→ TriggerProcessor
        │        └── 条件匹配 → 执行 context tool → 写入 WorkspaceContext
        │
        └──→ Worker 消费 → Executor.run()
                                │
                    ┌───────────┴────────────┐
                    ▼                        ▼
             ContextStore (内存)      Global Context (DB + pgvector)
             ←── load ── WorkspaceContext (DB)     ←── Skill/Knowledge Celery 任务
             ─── sync ──→ WorkspaceContext (DB)
```

---

## 关键设计原则

| 原则 | 说明 |
|------|------|
| **渐进式披露** | glance → summary → content 三层信息密度，按需加载 |
| **双层存储** | WorkspaceContext = DB 持久化 + ContextStore 内存缓存 |
| **向量多维** | 384/768/1024/1536 四种维度兼容不同 Embedding 模型 |
| **软删除** | WorkspaceContext / Document 均用 `is_deleted` 标记，保留历史 |
| **路径寻址** | 层级路径字符串（如 `tools/search/web`）代替嵌套对象树 |
| **事件驱动** | Trigger 响应 Event 自动操作 Context，形成自动化闭环 |
| **用户隔离** | 绝大多数实体以 `user_id` 隔离，支持多租户 |
| **Trie 索引** | ContextStore 内部用前缀树加速 glob/children 查询 |
