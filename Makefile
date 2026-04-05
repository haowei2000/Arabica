# Makefile for AI630 Project
# 提供便捷的开发和测试命令

.PHONY: help test test-unit test-integration test-slow test-coverage test-fast test-verbose test-failed clean install install-dev lint lint-fix format format-check check db-migrate db-upgrade db-rollback db-downgrade db-reset db-revision db-revision-empty db-current db-history db-heads db-branches db-stamp db-status run run-mcp dev dev-mcp docker-build docker-build-cache docker-build-multi docker-build-push docker-build-context-service docker-up docker-up-infra docker-down docker-logs docker-restart docker-shell docker-status docker-stats docker-size docker-inspect-layers docker-clean docker-prune-all docker-build-dev docker-scan docker-info docker-test-build env-sync ci pre-commit quick-test full-test shell deps-update deps-tree info

# 默认目标
.DEFAULT_GOAL := help

# 颜色定义
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## 显示帮助信息
	@echo "$(BLUE)AI630 项目 - 可用命令:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ============================================================================
# 安装和依赖管理
# ============================================================================

docker-build: ## Build Docker image (backend)
	cd docker && docker build -f Dockerfile -t structure-service:latest ..

docker-build-frontend: ## Build Docker image (frontend)
	cd docker && docker build -f Dockerfile.frontend -t structure-frontend:latest ..

docker-build-context-service: ## Build Docker image (context-service)
	cd docker && docker build -f Dockerfile.context-service -t structure-context-service:latest ..

docker-build-all: ## Build all Docker images (backend + frontend + context-service)
	$(MAKE) docker-build
	$(MAKE) docker-build-frontend
	$(MAKE) docker-build-context-service

docker-up: ## Start all containers (infra + app + celery worker)
	cd docker && docker compose -p structure --env-file .env --profile celery up -d

docker-up-infra: ## Start infrastructure only (postgres, redis, rustfs, context-service)
	cd docker && COMPOSE_PROFILES=infra docker compose -p structure --env-file .env up -d

docker-deploy: ## Pull pre-built images and restart all containers (used by CD)
	docker ps -aq --filter "network=structure-network" | xargs -r docker rm -f || true
	docker network rm structure-network || true
	cd docker && docker compose -p structure --env-file .env up -d --no-build
	cd docker && docker compose -p structure --env-file .env run --rm tool-initializer || true
	docker image prune -f

dev-infra: docker-up-infra ## Start infra in docker, run migrations, and prepare for local development
	@echo "$(BLUE)等待数据库就绪...$(NC)"
	@sleep 3
	$(MAKE) db-upgrade
	@echo "$(GREEN)基础设施已就绪。现在你可以运行 'make start-api', 'make start-worker', 'make start-frontend' 等命令进行本地开发。$(NC)"

dev-local: dev-infra ## Start infra in docker and then start all other services locally
	$(MAKE) start-all

docker-down: ## Stop all containers
	cd docker && docker compose -p structure --env-file .env --profile all down

docker-status: ## Show container status
	cd docker && docker compose -p structure --env-file .env ps

docker-logs: ## Show container logs
	cd docker && docker compose -p structure --env-file .env logs -f

# ============================================================================
# 环境配置
# ============================================================================

env-sync: ## 同步 .env.example 到 .env
	uv run sync-env

env-sync-docker: ## 同步 .env.example 到 docker/.env
	uv run sync-env --docker

# ============================================================================
# 数据库迁移 (Alembic)
# ============================================================================

db-upgrade: ## 升级数据库到最新版本
	uv run alembic upgrade head

db-downgrade: ## 回滚数据库一个版本
	uv run alembic downgrade -1

db-revision: ## 创建新的迁移脚本 (自动检测变更)
	@read -p "迁移描述: " msg; \
	uv run alembic revision --autogenerate -m "$$msg"

db-revision-empty: ## 创建空的迁移脚本
	@read -p "迁移描述: " msg; \
	uv run alembic revision -m "$$msg"

db-current: ## 显示当前数据库版本
	uv run alembic current

db-history: ## 显示迁移历史
	uv run alembic history --verbose

db-heads: ## 显示所有分支头
	uv run alembic heads

db-reset: ## 重置数据库 (危险: 回滚所有迁移)
	@echo "$(RED)警告: 这将回滚所有迁移!$(NC)"
	@read -p "确认继续? [y/N] " confirm; \
	if [ "$$confirm" = "y" ]; then \
		uv run alembic downgrade base; \
		uv run alembic upgrade head; \
	fi

db-status: ## 显示迁移状态
	@echo "$(BLUE)当前版本:$(NC)"
	@uv run alembic current
	@echo ""
	@echo "$(BLUE)待执行迁移:$(NC)"
	@uv run alembic history --indicate-current


# ============================================================================
# 服务启动
# ============================================================================

start-api: ## 启动 API 服务 (端口 8000)
	cd src && uv run structure-api

start-worker: ## 启动 Worker 服务
	cd src && uv run structure-worker

start-celery: ## 启动 Celery Worker
	cd src && uv run structure-celery worker --concurrency=4 --loglevel=info

start-celery-beat: ## 启动 Celery Beat 调度器
	cd src && uv run structure-celery beat --loglevel=info

start-mcp: ## 启动 MCP 服务 (端口 9000)
	cd src && uv run structure-mcp

start-frontend: ## 启动前端开发服务器
	cd frontend && npm run dev

start-all: ## 启动所有服务 (API, Worker, Celery, Frontend)
	@echo "$(BLUE)启动所有服务...$(NC)"
	@echo "$(YELLOW)提示: 每个服务将在后台运行，使用 'make stop-all' 停止所有服务$(NC)"
	@echo ""
	@# 先运行数据库迁移
	uv run alembic upgrade head
	@# 启动后端服务
	cd src && uv run structure-api & \
	cd src && uv run structure-worker & \
	cd src && uv run structure-celery worker --concurrency=4 --loglevel=info & \
	cd frontend && npm run dev & \
	wait

resync-tools: ## 重新同步所有工具到 Context/WorkspaceContext 表
	cd src && uv run python -c "from structure.celery_worker.tasks.context_sync_tasks import resync_all_tools_to_contexts; resync_all_tools_to_contexts()"

reset-admin-password: ## Sync admin password from docker/.env into the running database
	@PASSWORD=$$(grep '^AUTH__ADMIN_PASSWORD=' docker/.env | cut -d= -f2); \
	USERNAME=$$(grep '^AUTH__ADMIN_USERNAME=' docker/.env | cut -d= -f2); \
	USERNAME=$${USERNAME:-admin}; \
	echo "==> Updating password for user: $$USERNAME"; \
	printf 'import asyncio\nfrom structure.extensions.database import get_session\nfrom structure.utils.security import hash_password\nfrom sqlalchemy import update\nfrom structure.models.auth.user import User\nasync def main():\n    async with get_session("structure") as s:\n        await s.execute(update(User).where(User.username == "%s").values(password_hash=hash_password("%s")))\n        await s.commit()\n        print("Done")\nasyncio.run(main())\n' "$$USERNAME" "$$PASSWORD" | docker exec -i structure-backend-1 python3; \
	echo "==> Password updated. No restart needed."

stop-all: ## 停止所有本地服务
	@echo "$(YELLOW)停止所有服务...$(NC)"
	@-pkill -f "structure-api" 2>/dev/null || true
	@-pkill -f "structure-worker" 2>/dev/null || true
	@-pkill -f "structure-celery" 2>/dev/null || true
	@-pkill -f "vite" 2>/dev/null || true
	@echo "$(GREEN)所有服务已停止$(NC)"

# ============================================================================
# 代码质量
# ============================================================================

lint: ## 检查代码风格 (ruff check)
	uv run ruff check src/

lint-fix: ## 自动修复代码风格问题 (ruff check --fix)
	uv run ruff check --fix src/

format: ## 格式化代码 (ruff format)
	uv run ruff format src/

format-check: ## 检查代码格式 (不修改文件)
	uv run ruff format --check src/

check: lint format-check ## 运行所有检查 (lint + format check)

# ============================================================================
# 测试
# ============================================================================

test: ## 运行所有单元测试
	uv run pytest tests/unit/ -q

test-coverage: ## 运行测试并生成覆盖率报告
	uv run pytest tests/unit/ --cov=src/structure --cov-report=html --cov-report=term-missing --cov-report=xml --cov-fail-under=60 -q

test-fast: ## 快速运行测试 (跳过慢速测试)
	uv run pytest tests/unit/ -q -m "not slow"

test-verbose: ## 详细输出运行测试
	uv run pytest tests/unit/ -v
