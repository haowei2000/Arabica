#!/usr/bin/env python3
"""
OAuth2认证演示脚本
展示如何使用OAuth2PasswordBearer进行API认证
"""

import json
import os
from pathlib import Path
import sys

import requests

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


def setup_environment():
    """设置环境变量"""
    os.environ.setdefault("ENV", "development")
    os.environ.setdefault("POSTGRES__HOST", "127.0.0.1")
    os.environ.setdefault("POSTGRES__PORT", "5432")
    os.environ.setdefault("POSTGRES__USERNAME", "postgres")
    os.environ.setdefault("POSTGRES__PASSWORD", "change-me-postgres-password")
    os.environ.setdefault("POSTGRES__STRUCTURE_DBNAME", "structure")
    print("环境变量已设置")


def login_and_get_token(base_url: str, username: str, password: str) -> str:
    """
    登录并获取访问令牌

    Args:
        base_url: API基础URL
        username: 用户名
        password: 密码

    Returns:
        访问令牌
    """
    login_url = f"{base_url}/api/auth/login"
    payload = {"username": username, "password": password}

    response = requests.post(login_url, json=payload)

    if response.status_code == 200:
        data = response.json()
        return data["access_token"]
    raise Exception(f"登录失败: {response.status_code} - {response.text}")


def access_protected_endpoint(base_url: str, token: str) -> dict:
    """
    访问受保护的端点

    Args:
        base_url: API基础URL
        token: 访问令牌

    Returns:
        响应数据
    """
    # 访问用户资料端点
    profile_url = f"{base_url}/api/user-examples/profile"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(profile_url, headers=headers)

    if response.status_code == 200:
        return response.json()
    raise Exception(f"访问受保护端点失败: {response.status_code} - {response.text}")


def access_me_endpoint(base_url: str, token: str) -> dict:
    """
    访问/me端点获取当前用户信息

    Args:
        base_url: API基础URL
        token: 访问令牌

    Returns:
        用户信息
    """
    me_url = f"{base_url}/api/auth/me"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(me_url, headers=headers)

    if response.status_code == 200:
        return response.json()
    raise Exception(f"访问/me端点失败: {response.status_code} - {response.text}")


def main():
    """主函数"""
    print("=== OAuth2认证演示 ===")

    # 设置环境变量
    setup_environment()

    # API基础URL
    base_url = "http://localhost:8000"

    # 用户凭据
    username = "admin"
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError("ADMIN_PASSWORD must be set")

    try:
        # 1. 登录获取访问令牌
        print(f"\n1. 使用凭据登录: {username}/{password}")
        token = login_and_get_token(base_url, username, password)
        print("✓ 成功获取访问令牌")
        print(f"  令牌长度: {len(token)} 字符")

        # 2. 使用令牌访问受保护的端点
        print("\n2. 访问受保护的用户资料端点")
        profile_data = access_protected_endpoint(base_url, token)
        print("✓ 成功获取用户资料:")
        print(f"  用户ID: {profile_data.get('user_id')}")
        print(f"  角色: {profile_data.get('role')}")
        print(f"  租户ID: {profile_data.get('tenant_id')}")

        # 3. 使用令牌访问/me端点
        print("\n3. 访问/me端点获取当前用户信息")
        me_data = access_me_endpoint(base_url, token)
        print("✓ 成功获取当前用户信息:")
        print(f"  用户名: {me_data.get('username')}")
        print(f"  邮箱: {me_data.get('email')}")
        print(f"  角色: {me_data.get('role')}")
        print(f"  是否超级用户: {me_data.get('is_superuser')}")
        print(f"  是否激活: {me_data.get('is_active')}")

        print("\n=== 演示完成 ===")
        return 0

    except Exception as e:
        print(f"✗ 错误: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
