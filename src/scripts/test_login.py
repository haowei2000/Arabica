#!/usr/bin/env python3
"""
测试登录流程的脚本
"""

import json
import os
from pathlib import Path
import subprocess
import sys

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


def setup_environment():
    """设置环境变量"""
    os.environ.setdefault("ENV", "development")
    os.environ.setdefault("POSTGRES__HOST", "10.1.2.111")
    os.environ.setdefault("POSTGRES__PORT", "5435")
    os.environ.setdefault("POSTGRES__USERNAME", "postgres")
    os.environ.setdefault("POSTGRES__PASSWORD", "difyai123456")
    os.environ.setdefault("POSTGRES__STRUCTURE_DBNAME", "structure")
    print("环境变量已设置")


def check_user_exists():
    """检查管理员用户是否存在"""
    try:
        # 使用psql检查用户是否存在
        cmd = [
            "psql",
            "-h",
            os.environ["POSTGRES__HOST"],
            "-p",
            os.environ["POSTGRES__PORT"],
            "-U",
            os.environ["POSTGRES__USERNAME"],
            "-d",
            os.environ["POSTGRES__STRUCTURE_DBNAME"],
            "-t",
            "-c",
            "SELECT username FROM auth_user WHERE username = 'admin';",
        ]

        # 设置PGPASSWORD环境变量
        env = os.environ.copy()
        env["PGPASSWORD"] = os.environ["POSTGRES__PASSWORD"]

        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode == 0 and result.stdout.strip():
            print("✓ 管理员用户已存在")
            return True
        print("✗ 管理员用户不存在")
        return False
    except Exception as e:
        print(f"检查用户时出错: {e}")
        return False


def run_migrations():
    """运行Alembic迁移"""
    try:
        print("运行Alembic迁移...")
        result = subprocess.run(
            ["alembic", "upgrade", "head"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print("✓ 迁移成功完成")
            return True
        print(f"✗ 迁移失败: {result.stderr}")
        return False
    except Exception as e:
        print(f"运行迁移时出错: {e}")
        return False


def main():
    """主函数"""
    print("=== 登录测试脚本 ===")

    # 设置环境变量
    setup_environment()

    # 检查用户是否存在
    user_exists = check_user_exists()

    # 如果用户不存在，运行迁移
    if not user_exists:
        print("管理员用户不存在，运行迁移...")
        if run_migrations():
            # 再次检查用户
            user_exists = check_user_exists()
        else:
            print("迁移失败，无法创建管理员用户")
            return 1

    if user_exists:
        print("\n=== 登录信息 ===")
        print("用户名: admin")
        print("密码: admin123")
        print("\n使用以下命令登录:")
        print('curl -X POST "http://localhost:8000/api/auth/login" \\')
        print('  -H "Content-Type: application/json" \\')
        print('  -d \'{"username": "admin", "password": "admin123"}\'')
        return 0
    print("无法创建或找到管理员用户")
    return 1


if __name__ == "__main__":
    sys.exit(main())
