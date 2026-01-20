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

docker-up:
	docker compose -f docker/docker-compose.yml --env-file .env up -d

docker-down:
	docker compose -f docker/docker-compose.yml --env-file .env down