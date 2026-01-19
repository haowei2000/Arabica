#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Worker 启动脚本 (Celery)

运行方式:
    python src/scripts/run_agent_worker.py

或使用 CLI:
    aiwen-worker

或直接使用 Celery:
    celery -A aiwen.celery_app worker --loglevel=info
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 加载环境变量 - CRITICAL: Must be before importing any aiwen modules!
from dotenv import load_dotenv
env_file = project_root / "src" / ".env"
print(f"Loading environment from: {env_file}")
if env_file.exists():
    load_dotenv(env_file)
    print("Environment variables loaded")
else:
    print(f"Warning: .env file not found at {env_file}")
    print("   Worker may fail if environment variables are not set!")

# Use the worker CLI
from aiwen.worker_cli import main_sync


if __name__ == "__main__":
    main_sync()
