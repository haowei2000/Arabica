# Agent Worker 快速启动指南

## 🚀 快速启动

### 启动 Worker

```bash
# 方法 1: 直接启动（推荐用于开发/调试）
python src/structure/worker_cli.py

# 方法 2: 模块方式启动
python -m structure.start_worker

# 方法 3: 后台运行（生产环境）
nohup python src/structure/worker_cli.py > logs/agent_worker.log 2>&1 &
```

### 管理 Worker

```bash
# 查看 Worker 进程
ps aux | grep start_worker | grep -v grep

# 停止 Worker
pkill -f "start_worker.py"

# 查看实时日志
tail -f logs/agent_worker.log
```

## ✅ 启动成功标志

成功启动后，你会看到以下输出：

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
✅ Database session factory configured for 'aiwen' database
============================================================
🚀 Starting Worker Loop...
============================================================
Starting AgentWorker...
```

**关键检查点:**
- ✅ 环境变量已加载
- ✅ Redis 客户端初始化成功
- ✅ Agent Registry 初始化成功
- ✅ DEFAULT001 模板已注册
- ✅ Worker 循环已启动

## 🐛 常见问题

### 问题 1: 环境变量未加载

**错误信息:**
```
ValidationError: 2 validation errors for AppSettings
postgres
  Field required
```

**解决方案:**
确保 `src/.env` 文件存在并包含所有必需的配置。

### 问题 2: DEFAULT001 未注册

**错误信息:**
```
Agent template 'DEFAULT001' is not registered in AgentRegistry
```

**解决方案:**
这个问题已经在最新版本中修复。确保使用 `src/aiwen/start_worker.py` 启动。

### 问题 3: 数据库连接失败

**错误信息:**
```
Database 'primary' is not configured
```

**解决方案:**
这个问题已修复。新版本会自动使用 `aiwen` 数据库。

## 📋 使用 screen 管理 Worker（推荐）

### 首次启动

```bash
# 创建一个新的 screen 会话
screen -S agent_worker

# 启动 worker
python src/structure/worker_cli.py

# 按 Ctrl+A 然后按 D 离开 screen（worker 继续运行）
```

### 重新连接

```bash
# 查看所有 screen 会话
screen -ls

# 重新连接到 agent_worker 会话
screen -r agent_worker
```

### 停止 Worker

```bash
# 重新连接到会话
screen -r agent_worker

# 按 Ctrl+C 停止 worker

# 退出 screen
exit
```

## 📋 使用 tmux 管理 Worker

### 首次启动

```bash
# 创建新的 tmux 会话
tmux new -s agent_worker

# 启动 worker
python src/structure/worker_cli.py

# 按 Ctrl+B 然后按 D 离开（worker 继续运行）
```

### 重新连接

```bash
# 查看所有 tmux 会话
tmux ls

# 重新连接到 agent_worker 会话
tmux attach -t agent_worker
```

## 🔍 监控和调试

### 实时查看日志

```bash
# 查看最新日志
tail -f logs/agent_worker.log

# 查看最近 100 行
tail -100 logs/agent_worker.log

# 搜索错误
grep ERROR logs/agent_worker.log
```

### 检查 Worker 状态

```bash
# 查看进程
ps aux | grep start_worker

# 查看端口占用（如果有）
lsof -i :8000
```

## 📚 相关文档

- [Agent Worker 详细文档](./AGENT_WORKER.md)
- [问题修复总结](./FIXES_SUMMARY.md)
- [重构文档](./REFACTORING_MANAGER.md)

## 🆘 获取帮助

如果遇到问题：

1. 检查日志文件 `logs/agent_worker.log`
2. 确认环境变量配置正确
3. 确认 Redis 和数据库服务正常运行
4. 查看相关文档了解详细信息
