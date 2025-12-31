# 认证授权系统使用说明

## 系统概述

本系统实现了基于JWT和Refresh Token的认证授权机制，支持多租户架构。主要特性包括：

1. 用户注册和登录
2. JWT Access Token和Refresh Token机制
3. 基于角色的访问控制(RBAC)
4. 多租户支持

## API端点

### 认证相关端点

#### 1. 用户注册
```
POST /api/auth/register
```

请求体:
```json
{
  "username": "testuser",
  "email": "test@example.com",
  "password": "password123"
}
```

#### 2. 用户登录
```
POST /api/auth/login
```

请求体:
```json
{
  "username": "testuser",
  "password": "password123"
}
```

响应:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

#### 3. 刷新Token
```
POST /api/auth/refresh
```

请求体:
```json
{
  "refresh_token": "eyJ..."
}
```

#### 4. 获取当前用户信息
```
GET /api/auth/me
```

需要在Authorization头部提供Bearer Token:
```
Authorization: Bearer eyJ...
```

### 测试端点

#### 1. 公共端点（无需认证）
```
GET /api/test-auth/public
```

#### 2. 受保护端点（需要认证）
```
GET /api/test-auth/protected
```

需要在Authorization头部提供Bearer Token

#### 3. 管理员端点（需要admin角色）
```
GET /api/test-auth/admin-only
```

需要在Authorization头部提供具有admin角色的Bearer Token

### 用户依赖注入示例端点

#### 1. 获取用户资料
```
GET /api/user-examples/profile
```

#### 2. 安全用户资料（检查用户活跃状态）
```
GET /api/user-examples/secure-profile
```

#### 3. 管理员面板
```
GET /api/user-examples/admin-panel
```

#### 4. 版主区域（管理员和版主可访问）
```
GET /api/user-examples/moderator-area
```

#### 5. 自定义角色检查（例如：高级用户）
```
GET /api/user-examples/custom-role-check
```

#### 6. 同时访问请求和用户信息
```
GET /api/user-examples/request-and-user
```

## 使用示例

### 1. 用户注册
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "password": "password123"
  }'
```

### 2. 用户登录
```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123"
  }'
```

### 3. 访问受保护资源
```bash
curl -X GET "http://localhost:8000/api/test-auth/protected" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN_HERE"
```

## 配置说明

### 环境变量

在 `.env` 文件中配置以下环境变量：

```bash
# JWT配置
JWT_SECRET_KEY=your-super-secret-jwt-key-here-change-in-production
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
```

## 权限装饰器使用

在路由处理器中可以使用权限装饰器：

### 1. 需要认证
```python
from aiwen.utils.permissions import require_auth

@router.get("/protected")
@require_auth
async def protected_endpoint(request: Request):
    return {"message": "This is protected"}
```

### 2. 需要特定角色
```python
from aiwen.utils.permissions import require_roles

@router.get("/admin-only")
@require_roles(["admin"])
async def admin_only_endpoint(request: Request):
    return {"message": "Admin only"}
```

## 依赖注入使用

系统提供了多种依赖注入方式来获取当前用户信息：

### 1. 基本用户信息获取
```python
from fastapi import Depends
from aiwen.utils.current_user import get_current_user
from aiwen.schemas.auth.auth import TokenData

@router.get("/profile")
async def get_profile(current_user: TokenData = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id
    }
```

### 2. 获取活跃用户
```python
from aiwen.utils.current_user import get_current_active_user

@router.get("/secure-profile")
async def get_secure_profile(current_user: TokenData = Depends(get_current_active_user)):
    return {"message": "This user is active"}
```

### 3. 角色检查依赖
```python
from aiwen.utils.current_user import get_current_admin_user, require_any_role

# 管理员专用
@router.get("/admin-panel")
async def admin_panel(current_user: TokenData = Depends(get_current_admin_user)):
    return {"message": "Admin access"}

# 多角色检查
@router.get("/moderator-area")
async def moderator_area(current_user: TokenData = Depends(require_any_role(["admin", "moderator"]))):
    return {"message": "Moderator access"}
```

### 4. 自定义角色依赖
```python
from aiwen.utils.current_user import require_role

@router.get("/premium-content")
async def premium_content(current_user: TokenData = Depends(require_role("premium"))):
    return {"message": "Premium content unlocked"}
```

### 5. 同时访问请求和用户信息
```python
from fastapi import Request, Depends
from aiwen.utils.current_user import get_current_user

@router.get("/request-info")
async def request_info(
    request: Request,
    current_user: TokenData = Depends(get_current_user)
):
    return {
        "user_id": current_user.user_id,
        "method": request.method,
        "path": request.url.path
    }
```

## 数据模型

### User模型
- id: UUID (主键)
- username: 用户名
- email: 邮箱
- password_hash: 密码哈希
- tenant_id: 租户ID
- role: 用户角色
- is_active: 是否激活
- created_at: 创建时间
- updated_at: 更新时间

### Tenant模型
- id: UUID (主键)
- name: 租户名称
- description: 租户描述
- is_active: 是否激活
- created_at: 创建时间
- updated_at: 更新时间

## 安全特性

1. 密码使用bcrypt进行哈希存储
2. JWT Token有有效期限制
3. Refresh Token有效期更长，用于获取新的Access Token
4. 基于角色的访问控制
5. 请求头验证和Token验证

## 注意事项

1. 在生产环境中，请务必更改JWT_SECRET_KEY为强随机密钥
2. 建议使用HTTPS来保护Token传输
3. 定期轮换JWT密钥以提高安全性
4. 根据实际需求调整Token过期时间