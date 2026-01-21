# Makefile for AI630 Project
# 提供便捷的开发和测试命令

.PHONY: help test test-unit test-integration test-slow test-coverage test-fast test-verbose test-failed clean install install-dev lint lint-fix format format-check check db-migrate db-upgrade db-rollback db-downgrade db-reset db-revision db-revision-empty db-current db-history db-heads db-branches db-stamp db-status run run-mcp dev dev-mcp docker-build docker-build-cache docker-build-multi docker-build-push docker-up docker-down docker-logs docker-restart docker-shell docker-status docker-stats docker-size docker-inspect-layers docker-clean docker-prune-all docker-build-dev docker-scan docker-info docker-test-build env-sync ci pre-commit quick-test full-test shell deps-update deps-tree info

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

docker-up: ## 启动 Docker 容器
	docker compose -f docker/docker-compose.yml --env-file .env up -d

docker-down: ## 停止 Docker 容器
	docker compose -f docker/docker-compose.yml --env-file .env down

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