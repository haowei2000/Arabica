# 🌐 局域网访问配置指南

解决"只能本地访问，局域网无法访问"的问题。

## 🔍 问题诊断

### 快速检查清单

运行以下命令检查当前状态：

```bash
# 1. 检查服务监听地址
netstat -ano | findstr "8000"    # API 端口
netstat -ano | findstr "3000"    # 前端端口
netstat -ano | findstr ":80"     # nginx 端口

# 2. 获取本机局域网 IP
ipconfig | findstr "IPv4"

# 3. 测试本地访问
curl http://localhost:8000/api/health
curl http://localhost:3000

# 4. 从局域网其他设备测试（替换为你的 IP）
# curl http://192.168.1.100:8000/api/health
```

### 常见问题类型

| 症状 | 可能原因 | 解决方案 |
|------|---------|---------|
| 本地 ✓ 局域网 ✗ | 绑定到 127.0.0.1 | 改为 0.0.0.0 |
| 两者都 ✗ | 服务未启动 | 启动服务 |
| 连接超时 | 防火墙阻止 | 配置防火墙 |
| 拒绝连接 | 端口被占用 | 更换端口 |

## ✅ 解决方案

### 方案一：直接运行（开发环境）

#### 1. 后端 API 配置

你的后端已经正确配置为 `0.0.0.0`：

```python
# src/aiwen/api_cli.py (已正确)
uvicorn.run(
    "aiwen.main:app",
    host="0.0.0.0",  # ✓ 正确
    port=8000,
)
```

#### 2. 前端配置

你的前端也已经正确配置：

```typescript
// frontend/vite.config.ts (已正确)
server: {
  host: '0.0.0.0',  // ✓ 正确
  port: 3000,
}
```

#### 3. 启动服务

```bash
# 终端 1: 启动后端
cd src
uv run aiwen-api

# 终端 2: 启动前端
cd frontend
npm run dev
```

#### 4. 配置 Windows 防火墙

**方法 A: PowerShell（管理员）**

```powershell
# 允许端口 8000 (API)
New-NetFirewallRule -DisplayName "Aiwen API" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow

# 允许端口 3000 (前端)
New-NetFirewallRule -DisplayName "Aiwen Frontend" -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow

# 如果使用 nginx
New-NetFirewallRule -DisplayName "Aiwen Nginx" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow
```

**方法 B: 图形界面**

1. 打开 `Windows Defender 防火墙` → `高级设置`
2. 点击 `入站规则` → `新建规则`
3. 选择 `端口` → `下一步`
4. 选择 `TCP`，输入端口 `8000` → `下一步`
5. 选择 `允许连接` → `下一步`
6. 勾选所有配置文件 → `下一步`
7. 命名为 `Aiwen API` → `完成`
8. 重复步骤为端口 3000 创建规则

#### 5. 测试访问

```bash
# 获取你的局域网 IP
ipconfig

# 假设你的 IP 是 192.168.1.100
# 从其他设备访问：
# http://192.168.1.100:3000      # 前端
# http://192.168.1.100:8000/api  # API
```

---

### 方案二：使用 Docker + Nginx（生产环境）

#### 1. 修改 docker-compose.yml

确保端口正确映射到所有接口：

```yaml
# docker/docker-compose.yml
services:
  nginx:
    ports:
      - "0.0.0.0:80:80"  # 明确绑定到所有接口
      # 或使用环境变量
      # - "${NGINX_EXPOSE_PORT:-80}:80"
```

#### 2. 使用优化的 nginx 配置

```bash
# 使用新配置（已创建在 docker/nginx/nginx.conf.lan）
cd docker
cp nginx/nginx.conf.lan nginx/nginx.conf

# 或直接编辑 docker-compose.yml 使用新配置
```

#### 3. 更新 docker-compose.yml 端口映射

编辑 `docker/docker-compose.yml`：

```yaml
services:
  aiwen-app:
    ports:
      - "0.0.0.0:8000:8000"  # 明确指定
      - "0.0.0.0:9000:9000"

  aiwen-frontend:
    ports:
      - "0.0.0.0:3000:80"

  nginx:
    ports:
      - "0.0.0.0:80:80"
```

#### 4. 启动 Docker 服务

```bash
# 停止旧容器
make docker-down

# 启动新容器
docker compose -f docker/docker-compose.yml up -d

# 查看日志
docker compose -f docker/docker-compose.yml logs -f nginx
```

#### 5. 防火墙配置

```powershell
# 允许 Docker 容器端口
New-NetFirewallRule -DisplayName "Docker Nginx" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow
```

---

### 方案三：混合模式（推荐开发）

前端和后端直接运行，nginx 在 Docker 中作为统一入口。

#### 1. 创建简化的 docker-compose.yml

```yaml
# docker/docker-compose.dev.yml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    container_name: aiwen-nginx-dev
    ports:
      - "0.0.0.0:80:80"
    volumes:
      - ./nginx/nginx.conf.lan:/etc/nginx/nginx.conf:ro
    restart: unless-stopped
    network_mode: host  # 使用宿主机网络
```

#### 2. 启动

```bash
# 终端 1: 后端
cd src && uv run aiwen-api

# 终端 2: 前端
cd frontend && npm run dev

# 终端 3: nginx
docker compose -f docker/docker-compose.dev.yml up
```

#### 3. 访问

```
http://your-lan-ip/       # 前端（通过 nginx）
http://your-lan-ip/api    # API（通过 nginx）
```

---

## 🛠️ 故障排除

### 问题 1: 端口被占用

```bash
# 查找占用端口的进程
netstat -ano | findstr "8000"

# 终止进程（替换 PID）
taskkill /PID <PID> /F

# 或更换端口
export PORT=8001  # Linux/Mac
set PORT=8001     # Windows CMD
```

### 问题 2: Docker 无法访问宿主机服务

如果在 Linux 上运行 Docker：

```yaml
# 修改 nginx.conf 中的 upstream
upstream backend {
    server 172.17.0.1:8000;  # Linux Docker 网关
}

# 或使用 host.docker.internal (需要 Docker 20.10+)
upstream backend {
    server host.docker.internal:8000;
}
```

### 问题 3: 防火墙规则无效

```powershell
# 查看现有规则
Get-NetFirewallRule -DisplayName "Aiwen*"

# 删除旧规则
Remove-NetFirewallRule -DisplayName "Aiwen API"

# 重新创建
New-NetFirewallRule -DisplayName "Aiwen API" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

### 问题 4: 跨域错误（CORS）

如果前端和后端在不同端口：

```python
# src/aiwen/main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境
    # allow_origins=["http://192.168.1.100:3000"],  # 生产环境
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 📊 网络架构对比

### 架构 1: 直接访问
```
[局域网设备] → [防火墙] → [前端:3000] → [API:8000]
                             ↑
                             直接访问各端口
```

### 架构 2: Nginx 代理
```
[局域网设备] → [防火墙] → [Nginx:80] → [前端:3000]
                                      → [API:8000]
                             ↑
                        统一入口，单端口
```

### 架构 3: 完全 Docker
```
[局域网设备] → [防火墙] → [Nginx:80] → [Frontend容器]
                                      → [Backend容器]
                             ↑
                        容器编排，隔离环境
```

---

## ✅ 推荐配置

### 开发环境
```bash
# 1. 直接运行服务（已配置 0.0.0.0）
make start-api        # 后端
make start-frontend   # 前端

# 2. 配置防火墙（一次性）
# 运行上面的 PowerShell 命令

# 3. 访问
# http://<your-lan-ip>:3000
```

### 生产环境
```bash
# 使用 Docker + Nginx
docker compose -f docker/docker-compose.yml --profile all up -d

# 访问
# http://<your-lan-ip>
```

---

## 🧪 验证步骤

### 1. 本地验证
```bash
curl http://localhost:8000/api/health
curl http://localhost:3000
```

### 2. 局域网验证
```bash
# 从另一台设备
curl http://192.168.1.100:8000/api/health
curl http://192.168.1.100:3000

# 或在浏览器打开
# http://192.168.1.100:3000
```

### 3. 检查网络配置
```bash
# Windows
ipconfig /all
netstat -ano | findstr "LISTENING"

# 测试端口可达性
Test-NetConnection -ComputerName 192.168.1.100 -Port 8000
```

---

## 📝 配置文件位置

```
项目根目录/
├── src/aiwen/api_cli.py          # API 启动配置 (host=0.0.0.0)
├── frontend/vite.config.ts        # 前端配置 (host: '0.0.0.0')
├── docker/nginx/nginx.conf        # Nginx 原配置
├── docker/nginx/nginx.conf.lan    # Nginx 优化配置（新）
└── docker/docker-compose.yml      # Docker 编排配置
```

---

**快速解决**: 如果你的配置已经是 `0.0.0.0`，问题很可能是 **Windows 防火墙**。
运行 PowerShell 命令允许端口，然后从局域网设备访问即可！
