# FastAPI Authentication Guide

本项目使用 FastAPI 官方推荐的认证方式，基于 OAuth2 和 JWT token 的依赖注入模式。

## 目录

- [概述](#概述)
- [认证依赖](#认证依赖)
- [使用示例](#使用示例)
- [API 端点](#api-端点)
- [常见问题](#常见问题)

## 概述

我们使用以下 FastAPI 官方推荐的方式实现认证：

1. **OAuth2PasswordBearer** - 从 `Authorization` header 中提取 token
2. **依赖注入 (Dependency Injection)** - 通过 `Depends()` 在路由中声明认证需求
3. **JWT Token** - 使用 JSON Web Tokens 进行无状态认证

### 为什么不使用全局中间件？

FastAPI 官方推荐使用依赖注入而不是全局中间件，因为：

- ✅ **更清晰** - 每个端点明确声明是否需要认证
- ✅ **更灵活** - 不同端点可以有不同的认证要求
- ✅ **更好的文档** - Swagger UI 自动显示哪些端点需要认证
- ✅ **类型安全** - IDE 可以自动补全和类型检查
- ✅ **更容易测试** - 可以轻松 mock 依赖

## 认证依赖

所有认证依赖都在 `structure/dependencies/auth.py` 中定义：

### 1. `get_current_user`

获取当前已认证的用户完整信息（包括数据库查询）。

```python
from typing import Annotated
from fastapi import Depends
from structure.core.dependencies.auth import get_current_user
from structure.schemas.auth.user import UserResponse


@router.get("/profile")
async def get_profile(
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    return {
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role
    }
```

### 2. `get_current_active_user`

确保用户不仅已认证，而且账户状态为激活。

```python
from structure.core.dependencies.auth import get_current_active_user


@router.get("/admin")
async def admin_panel(
        current_user: Annotated[UserResponse, Depends(get_current_active_user)]
):
    # 只有激活的用户才能访问
    return {"input": "Admin panel"}
```

### 3. `get_token_data`

仅获取 token 中的声明数据，不进行数据库查询（更高效）。

```python
from structure.core.dependencies.auth import get_token_data
from structure.schemas.auth.auth import TokenData


@router.get("/quick-check")
async def quick_check(
        token_data: Annotated[TokenData, Depends(get_token_data)]
):
    # 只包含: user_id, role, tenant_id
    return {"user_id": token_data.user_id}
```

## 使用示例

### 基础认证示例

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from structure.core.dependencies.auth import get_current_user
from structure.schemas.auth.user import UserResponse

router = APIRouter()


@router.get("/protected")
async def protected_route(
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """需要认证才能访问的端点"""
    return {
        "input": "You are authenticated!",
        "user": current_user.username
    }


@router.get("/public")
async def public_route():
    """公开端点，无需认证"""
    return {"input": "This is public"}
```

### 角色检查示例

```python
from fastapi import HTTPException, status

@router.get("/admin-only")
async def admin_only(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    # 检查用户角色
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return {"input": "Admin access granted"}
```

### 多角色检查示例

```python
@router.get("/moderator-area")
async def moderator_area(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    allowed_roles = ["admin", "moderator"]

    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"One of these roles required: {allowed_roles}"
        )

    return {"input": "Moderator area"}
```

### 高效的 Token 检查

当你只需要 token 中的信息而不需要完整用户数据时：

```python
from structure.core.dependencies.auth import get_token_data


@router.get("/fast-check")
async def fast_check(
        token_data: Annotated[TokenData, Depends(get_token_data)]
):
    # 不查询数据库，直接从 token 获取
    return {
        "user_id": token_data.user_id,
        "role": token_data.role,
        "tenant_id": token_data.tenant_id
    }
```

### 组合 Request 和 User

```python
from fastapi import Request

@router.get("/detailed")
async def detailed_info(
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    return {
        "user": current_user.username,
        "ip": request.client.host,
        "path": request.url.path
    }
```

## API 端点

### 认证端点

#### POST `/api/auth/register`
注册新用户

**请求体:**
```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "secure_password"
}
```

#### POST `/api/auth/login`
用户登录，获取 access token 和 refresh token

**请求体 (表单):**
```
username: john_doe
password: secure_password
```

**响应:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1...",
  "refresh_token": "eyJ0eXAiOiJKV1...",
  "token_type": "bearer"
}
```

#### POST `/api/auth/refresh`
使用 refresh token 刷新 access token

**请求体:**
```json
{
  "refresh_token": "eyJ0eXAiOiJKV1..."
}
```

#### GET `/api/auth/me`
获取当前用户信息（需要认证）

**Headers:**
```
Authorization: Bearer eyJ0eXAiOiJKV1...
```

### 测试端点

#### GET `/api/test-auth/public`
公开端点，无需认证

#### GET `/api/test-auth/protected`
受保护端点，需要认证

#### GET `/api/test-auth/admin-only`
仅管理员可访问

#### GET `/api/test-auth/token-info`
显示 token 信息（不查询数据库）

## 客户端使用示例

### cURL

```bash
# 1. 登录获取 token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"

# 2. 使用 token 访问受保护端点
curl -X GET http://localhost:8000/api/test-auth/protected \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Python Requests

```python
import requests

# 登录
response = requests.post(
    "http://localhost:8000/api/auth/login",
    data={"username": "admin", "password": "admin123"}
)
tokens = response.json()
access_token = tokens["access_token"]

# 访问受保护端点
headers = {"Authorization": f"Bearer {access_token}"}
response = requests.get(
    "http://localhost:8000/api/test-auth/protected",
    headers=headers
)
print(response.json())
```

### JavaScript Fetch

```javascript
// 登录
const loginResponse = await fetch('http://localhost:8000/api/auth/login', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/x-www-form-urlencoded',
  },
  body: new URLSearchParams({
    username: 'admin',
    password: 'admin123'
  })
});
const { access_token } = await loginResponse.json();

// 访问受保护端点
const response = await fetch('http://localhost:8000/api/test-auth/protected', {
  headers: {
    'Authorization': `Bearer ${access_token}`
  }
});
const data = await response.json();
console.log(data);
```

## 常见问题

### Q: 如何排除某些端点不需要认证？

A: 直接不使用认证依赖即可。依赖注入的方式让每个端点自己决定是否需要认证。

```python
@router.get("/public")
async def public_endpoint():
    # 无需任何认证依赖
    return {"input": "Public"}

@router.get("/protected")
async def protected_endpoint(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    # 需要认证
    return {"user": current_user.username}
```

### Q: Token 过期时间是多久？

A:
- Access Token: 30 分钟
- Refresh Token: 7 天

可以在 `structure/services/auth/token_service.py` 中修改。

### Q: 如何在 Swagger UI 中测试认证？

1. 打开 http://localhost:8000/docs
2. 点击右上角的 "Authorize" 按钮
3. 先调用 `/api/auth/login` 获取 token
4. 在弹出的对话框中输入: `Bearer YOUR_ACCESS_TOKEN`
5. 点击 "Authorize"
6. 现在可以测试需要认证的端点了

### Q: 401 Unauthorized vs 403 Forbidden 的区别？

- **401 Unauthorized**: 用户未认证（没有提供 token 或 token 无效）
- **403 Forbidden**: 用户已认证但没有权限（如非管理员访问管理员端点）

### Q: 如何创建自定义的角色依赖？

```python
from structure.core.dependencies.auth import get_current_user


def require_role(required_role: str):
    """依赖工厂：创建特定角色检查依赖"""

    async def role_checker(
            current_user: Annotated[UserResponse, Depends(get_current_user)]
    ):
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required"
            )
        return current_user

    return role_checker


# 使用
@router.get("/premium")
async def premium_content(
        current_user: Annotated[UserResponse, Depends(require_role("premium"))]
):
    return {"input": "Premium content"}
```

### Q: nl2sql API 为什么不需要认证？

nl2sql 相关的 API 端点被明确设计为公开 API，不需要认证。这是在业务层面决定的。如果将来需要添加认证，只需在路由函数中添加相应的依赖即可。

## 相关文件

- `src/structure/dependencies/auth.py` - 认证依赖定义
- `src/structure/routers/auth.py` - 认证相关路由（登录、注册等）
- `src/structure/routers/test_auth.py` - 认证功能测试路由
- `src/structure/routers/user_examples.py` - 使用示例路由
- `src/structure/services/auth/token_service.py` - Token 服务
- `src/structure/utils/jwt_utils.py` - JWT 工具函数

## 迁移说明

如果你的代码之前使用了全局 AuthMiddleware，需要进行以下更改：

**之前（中间件方式）：**
```python
@router.get("/protected")
async def protected(request: Request):
    user = request.state.user  # 从中间件设置的 state 获取
    return {"user_id": user.user_id}
```

**现在（依赖注入方式）：**
```python
@router.get("/protected")
async def protected(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    return {"user_id": current_user.id}
```

## 最佳实践

1. **使用类型注解** - 使用 `Annotated` 提供更好的类型提示
2. **选择合适的依赖** - 如果不需要完整用户信息，使用 `get_token_data`
3. **明确的角色检查** - 在函数内部检查角色，提供清晰的错误信息
4. **保护敏感端点** - 确保所有需要认证的端点都添加了依赖
5. **文档完整** - 在每个端点的 docstring 中说明认证要求
