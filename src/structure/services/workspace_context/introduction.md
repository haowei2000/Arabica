⏺ 这个 WorkspaceContextService 层的作用是在内存高性能的 ContextStore                                                         
  和持久化的数据库之间架起一座桥梁。让我详细解释为什么需要它：                                                             
                                                                                                                             
  核心问题                                                                                                                   
                                                                                                                             
  Context Layer 的局限                                                                                                       

  ContextStore (context_layer.py) 虽然强大，但它是纯内存的：
  - ✅ 极快的查询性能（Trie 索引）
  - ✅ 强大的 glob、tree、children 等 API
  - ❌ 服务器重启后数据丢失
  - ❌ 无法持久化保存
  - ❌ 多实例无法共享数据

  WorkspaceContextService 的三大职责

  1. 持久化存储

  架构：
  WorkspaceContext (DB) ←→ ContextStore (Memory)
      持久层                   缓存层

  # 写入时自动双写
  await service.set("tools/search_tool",
      glance="搜索工具 ✅",
      overview={"类型": "web_search", "状态": "可用"}
  )

  # 背后发生：
  # 1. 更新 ContextStore (内存) - 快速查询
  # 2. 同步到 WorkspaceContext (DB) - 持久化保存

  2. 启动恢复

  # 服务器重启后
  service = WorkspaceContextService(session, workspace_id)
  await service.load()  # 从 DB 重建 ContextStore

  # 现在可以继续使用高性能查询
  tools = await service.glob("tools/**")
  tree = await service.tree("tools")

  3. 自动同步策略

  ┌──────────┬──────────────┬──────────┬────────────────────────────────────┐
  │ 操作类型 │ ContextStore │ Database │                说明                │
  ├──────────┼──────────────┼──────────┼────────────────────────────────────┤
  │ 写操作   │ 立即更新     │ 立即同步 │ set(), delete() 触发双写           │
  ├──────────┼──────────────┼──────────┼────────────────────────────────────┤
  │ 读操作   │ 直接查询     │ 不访问   │ get(), glob(), tree() 零数据库开销 │
  ├──────────┼──────────────┼──────────┼────────────────────────────────────┤
  │ 初始加载 │ 从 DB 加载   │ 读取一次 │ load() 在启动/重启时调用           │
  └──────────┴──────────────┴──────────┴────────────────────────────────────┘

  为什么不直接用数据库查询？

  性能对比

  ┌───────────────────────┬────────────────────────────────┬────────────────┐
  │         操作          │            直接 SQL            │  ContextStore  │
  ├───────────────────────┼────────────────────────────────┼────────────────┤
  │ 路径查询 tools/search │ WHERE path = ?                 │ Hash O(1)      │
  ├───────────────────────┼────────────────────────────────┼────────────────┤
  │ Glob tools/**         │ WHERE path LIKE ? + 应用层过滤 │ Trie 遍历 O(m) │
  ├───────────────────────┼────────────────────────────────┼────────────────┤
  │ Tree 结构             │ 递归查询 + JOIN                │ 一次遍历构建   │
  ├───────────────────────┼────────────────────────────────┼────────────────┤
  │ 子节点                │ WHERE path LIKE 'prefix/%'     │ Trie 直接获取  │
  └───────────────────────┴────────────────────────────────┴────────────────┘

  ContextStore 的 Trie 索引 专为路径查询优化，比 SQL LIKE 快得多。

  架构分层清晰

  ┌─────────────────────────────────────────────┐
  │  API Layer (routers/context/)               │
  │  - REST endpoints                           │
  │  - 接收 HTTP 请求                            │
  └───────────────┬─────────────────────────────┘
                  │
  ┌───────────────▼─────────────────────────────┐
  │  Service Layer (WorkspaceContextService)    │
  │  - 持久化管理                                │
  │  - 自动同步策略                              │
  │  - Workspace 隔离                            │
  └───────────────┬─────────────────────────────┘
                  │
      ┌───────────┴────────────┐
      │                        │
  ┌───▼─────────┐      ┌──────▼──────────┐
  │ ContextStore│      │ WorkspaceContext│
  │  (Memory)   │      │  (PostgreSQL)   │
  │  - 快速查询  │      │  - 持久存储     │
  │  - Trie索引  │      │  - 事务保证     │
  └─────────────┘      └─────────────────┘

  实际使用场景

  Executor 运行时上下文

  # Executor 启动时
  service = WorkspaceContextService(session, workspace_id)
  await service.load()  # 恢复上次运行状态

  # Agent 查询可用工具（极快）
  tools = await service.glob("tools/**")

  # Agent 添加新发现（自动持久化）
  await service.set(
      "workspace_history/run_123/decision",
      glance="决策：使用搜索工具",
      detail={"reasoning": "...", "confidence": 0.95}
  )

  # 即使服务器崩溃，下次启动仍可恢复

  多 Workspace 隔离

  # 每个 workspace 有独立的上下文
  service_a = WorkspaceContextService(session, "workspace-a")
  service_b = WorkspaceContextService(session, "workspace-b")

  # 互不干扰
  await service_a.set("tools/custom", ...)
  await service_b.glob("tools/**")  # 不会看到 workspace-a 的数据

  关键设计模式

  1. 延迟加载

  def __init__(self, session, workspace_id):
      self._store = None  # 不立即创建
      self._loaded = False

  async def _ensure_loaded(self):
      if not self._loaded:
          await self.load()  # 首次访问时加载

  2. 写时同步（Write-Through Cache）

  async def set(self, path, glance, ...):
      # 1. 更新缓存
      self._store.set(path, glance, ...)

      # 2. 同步到 DB
      await self._sync_to_db(path, glance, ...)

  3. Schema 节点预注册

  async def _register_schemas(self):
      # 为标准路径注册聚合器
      self._store.schema(
          "tools",
          glance="Available Tools",
          aggregator=count_aggregator  # 自动统计工具数量
      )

  总结

  ┌────────────────┬─────────────────────┬───────────────────────────────────┐
  │      特性      │ ContextStore (Core) │ WorkspaceContextService (Service) │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ 查询性能       │ 极快（Trie 索引）   │ 继承 ContextStore 性能            │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ 持久化         │ ❌ 内存             │ ✅ PostgreSQL                     │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ 重启恢复       │ ❌ 数据丢失         │ ✅ 自动恢复                       │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ Workspace 隔离 │ ❌ 无概念           │ ✅ 基于 workspace_id              │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ 事务保证       │ ❌ 无               │ ✅ DB 事务                        │
  ├────────────────┼─────────────────────┼───────────────────────────────────┤
  │ 使用复杂度     │ 简单（纯内存）      │ 中等（需初始化）                  │
  └────────────────┴─────────────────────┴───────────────────────────────────┘

  WorkspaceContextService 让你既能享受 ContextStore 的高性能查询，又能获得数据库的持久化保证！ 这是典型的 Cache-Aside
  Pattern（旁路缓存模式）在上下文管理中的应用。