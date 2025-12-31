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

install: ## 安装项目依赖
	@echo "$(BLUE)安装项目依赖...$(NC)"
	uv sync

install-dev: ## 安装开发依赖
	@echo "$(BLUE)安装开发依赖...$(NC)"
	uv sync --group dev

# ============================================================================
# 测试命令
# ============================================================================

test: ## 运行所有测试
	@echo "$(BLUE)运行所有测试...$(NC)"
	uv run pytest

test-unit: ## 只运行单元测试
	@echo "$(BLUE)运行单元测试...$(NC)"
	uv run pytest -m unit

test-integration: ## 运行集成测试
	@echo "$(BLUE)运行集成测试...$(NC)"
	uv run pytest --run-integration -m integration

test-e2e: ## 运行端到端测试
	@echo "$(BLUE)运行端到端测试...$(NC)"
	uv run pytest -m e2e

test-slow: ## 运行慢速测试
	@echo "$(BLUE)运行慢速测试...$(NC)"
	uv run pytest --run-slow -m slow

test-fast: ## 快速运行测试（跳过慢速测试）
	@echo "$(BLUE)快速运行测试（跳过慢速测试）...$(NC)"
	uv run pytest -m "not slow" -n auto

test-parallel: ## 并行运行测试
	@echo "$(BLUE)并行运行测试...$(NC)"
	uv run pytest -n auto

test-verbose: ## 详细输出运行测试
	@echo "$(BLUE)详细输出运行测试...$(NC)"
	uv run pytest -vv -s

test-failed: ## 只运行上次失败的测试
	@echo "$(BLUE)运行上次失败的测试...$(NC)"
	uv run pytest --lf

test-specific: ## 运行特定测试文件（使用 FILE=path/to/test.py）
	@echo "$(BLUE)运行指定测试: $(FILE)$(NC)"
	uv run pytest $(FILE) -vv

# ============================================================================
# 测试覆盖率
# ============================================================================

test-coverage: ## 运行测试并生成覆盖率报告
	@echo "$(BLUE)运行测试并生成覆盖率报告...$(NC)"
	uv run pytest --cov=src/aiwen --cov-report=html --cov-report=term-missing --cov-report=xml

coverage-report: ## 查看覆盖率报告
	@echo "$(BLUE)打开覆盖率报告...$(NC)"
	@if [ -f htmlcov/index.html ]; then \
		open htmlcov/index.html || xdg-open htmlcov/index.html || echo "$(YELLOW)请手动打开 htmlcov/index.html$(NC)"; \
	else \
		echo "$(RED)覆盖率报告不存在，请先运行 'make test-coverage'$(NC)"; \
	fi

# ============================================================================
# 代码质量检查
# ============================================================================

lint: ## 运行代码检查
	@echo "$(BLUE)运行代码检查...$(NC)"
	uv run ruff check src/aiwen tests

lint-fix: ## 自动修复代码问题
	@echo "$(BLUE)自动修复代码问题...$(NC)"
	uv run ruff check --fix src/aiwen tests

format: ## 格式化代码
	@echo "$(BLUE)格式化代码...$(NC)"
	uv run ruff format src/aiwen tests

format-check: ## 检查代码格式
	@echo "$(BLUE)检查代码格式...$(NC)"
	uv run ruff format --check src/aiwen tests

check: lint format-check ## 运行所有检查（代码检查 + 格式检查）

# ============================================================================
# 清理命令
# ============================================================================

clean: ## 清理生成的文件
	@echo "$(BLUE)清理生成的文件...$(NC)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@find . -type f -name ".coverage" -delete 2>/dev/null || true
	@rm -rf htmlcov/ 2>/dev/null || true
	@rm -rf .coverage 2>/dev/null || true
	@rm -rf coverage.xml 2>/dev/null || true
	@rm -rf report.xml 2>/dev/null || true
	@echo "$(GREEN)清理完成!$(NC)"

clean-all: clean ## 深度清理（包括虚拟环境）
	@echo "$(BLUE)深度清理...$(NC)"
	@rm -rf .venv/ 2>/dev/null || true
	@rm -rf uv.lock 2>/dev/null || true
	@echo "$(GREEN)深度清理完成!$(NC)"

# ============================================================================
# 数据库相关
# ============================================================================

db-migrate: ## 运行数据库迁移（升级到最新版本）
	@echo "$(BLUE)运行数据库迁移...$(NC)"
	uv run alembic upgrade head
	@echo "$(GREEN)数据库迁移完成!$(NC)"

db-upgrade: ## 升级到指定版本（使用 REV=revision_id）
	@echo "$(BLUE)升级数据库到版本: $(REV)$(NC)"
	uv run alembic upgrade $(REV)

db-rollback: ## 回滚一个版本
	@echo "$(BLUE)回滚数据库迁移...$(NC)"
	uv run alembic downgrade -1
	@echo "$(GREEN)数据库回滚完成!$(NC)"

db-downgrade: ## 降级到指定版本（使用 REV=revision_id）
	@echo "$(BLUE)降级数据库到版本: $(REV)$(NC)"
	uv run alembic downgrade $(REV)

db-reset: ## 重置数据库（降级到 base 再升级到 head）
	@echo "$(YELLOW)警告: 这将重置数据库!$(NC)"
	@read -p "确定要继续吗? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "$(BLUE)重置数据库...$(NC)"; \
		uv run alembic downgrade base && uv run alembic upgrade head; \
		echo "$(GREEN)数据库重置完成!$(NC)"; \
	fi

db-revision: ## 创建新的迁移文件（使用 MSG="message"）
	@echo "$(BLUE)创建新的迁移文件...$(NC)"
	@if [ -z "$(MSG)" ]; then \
		echo "$(RED)错误: 请提供迁移消息，例如: make db-revision MSG='add user table'$(NC)"; \
		exit 1; \
	fi
	uv run alembic revision --autogenerate -m "$(MSG)"
	@echo "$(GREEN)迁移文件创建完成!$(NC)"

db-revision-empty: ## 创建空的迁移文件（使用 MSG="message"）
	@echo "$(BLUE)创建空的迁移文件...$(NC)"
	@if [ -z "$(MSG)" ]; then \
		echo "$(RED)错误: 请提供迁移消息，例如: make db-revision-empty MSG='custom migration'$(NC)"; \
		exit 1; \
	fi
	uv run alembic revision -m "$(MSG)"
	@echo "$(GREEN)空迁移文件创建完成!$(NC)"

db-current: ## 显示当前数据库版本
	@echo "$(BLUE)当前数据库版本:$(NC)"
	uv run alembic current

db-history: ## 显示迁移历史
	@echo "$(BLUE)迁移历史:$(NC)"
	uv run alembic history

db-heads: ## 显示当前的头版本
	@echo "$(BLUE)当前头版本:$(NC)"
	uv run alembic heads

db-branches: ## 显示分支情况
	@echo "$(BLUE)迁移分支:$(NC)"
	uv run alembic branches

db-stamp: ## 标记数据库版本（使用 REV=revision_id）
	@echo "$(BLUE)标记数据库版本为: $(REV)$(NC)"
	@if [ -z "$(REV)" ]; then \
		echo "$(RED)错误: 请提供版本号，例如: make db-stamp REV=head$(NC)"; \
		exit 1; \
	fi
	uv run alembic stamp $(REV)
	@echo "$(GREEN)数据库版本标记完成!$(NC)"

db-status: ## 显示数据库状态（当前版本和待迁移的版本）
	@echo "$(BLUE)数据库状态:$(NC)"
	@echo ""
	@echo "$(GREEN)当前版本:$(NC)"
	@uv run alembic current
	@echo ""
	@echo "$(GREEN)最新版本:$(NC)"
	@uv run alembic heads
	@echo ""
	@echo "$(GREEN)迁移历史（最近5条）:$(NC)"
	@uv run alembic history -r-5:

# ============================================================================
# 应用运行
# ============================================================================

run-api: ## 运行应用
	@echo "$(BLUE)启动应用...$(NC)"
	cd src && uv run aiwen-api

run-mcp: ## 运行 MCP 服务
	@echo "$(BLUE)启动 MCP 服务...$(NC)"
	cd src && uv run aiwen-mcp
run-worker:
	@echo "$(BLUE)启动 Worker 服务...$(NC)"
	cd src && uv run aiwen-worker

run-all: ## 运行所有服务
	@echo "$(BLUE)启动所有服务...$(NC)"
	cd src && uv run aiwen-worker &
	cd src && uv run aiwen-api &
	cd src && uv run aiwen-mcp &
dev: ## 开发模式运行应用
	@echo "$(BLUE)开发模式启动应用...$(NC)"
	cd src && uv run aiwen-api

dev-mcp: ## 开发模式运行 MCP 服务
	@echo "$(BLUE)开发模式启动 MCP 服务...$(NC)"
	cd src && uv run aiwen-mcp

# ============================================================================
# Docker 相关（优化版本）
# ============================================================================

# Docker build variables
DOCKER_REGISTRY ?= localhost
DOCKER_IMAGE := aiwen-service
DOCKER_TAG ?= latest
BUILD_DATE := $(shell date +%Y%m%d)
GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
VERSION ?= ${BUILD_DATE}-${GIT_COMMIT}

# Docker build args
PYTHON_VERSION ?= 3.12
UV_VERSION ?= 0.5.24

docker-build: ## 使用优化的多阶段Dockerfile构建镜像
	@echo "$(BLUE)使用优化Dockerfile构建镜像...$(NC)"
	@echo "$(GREEN)镜像: $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG)$(NC)"
	DOCKER_BUILDKIT=1 docker build \
		--file docker/Dockerfile \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(VERSION) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg UV_VERSION=$(UV_VERSION) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--progress=plain \
		.
	@echo "$(GREEN)镜像构建完成!$(NC)"

docker-build-cache: ## 使用缓存的优化构建（更快）
	@echo "$(BLUE)使用缓存构建优化镜像...$(NC)"
	DOCKER_BUILDKIT=1 docker build \
		--file docker/Dockerfile \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(VERSION) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg UV_VERSION=$(UV_VERSION) \
		--cache-from $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):buildcache \
		--cache-from $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):latest \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		.
	@echo "$(GREEN)缓存构建完成!$(NC)"

docker-build-multi: ## 使用优化Dockerfile构建多架构镜像
	@echo "$(BLUE)构建多架构优化镜像 (amd64, arm64)...$(NC)"
	docker buildx create --use --name aiwen-builder --driver docker-container >/dev/null 2>&1 || true
	docker buildx build \
		--file docker/Dockerfile \
		--platform linux/amd64,linux/arm64 \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(VERSION) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg UV_VERSION=$(UV_VERSION) \
		--cache-from type=registry,ref=$(DOCKER_REGISTRY)/$(DOCKER_IMAGE):buildcache \
		--cache-to type=registry,ref=$(DOCKER_REGISTRY)/$(DOCKER_IMAGE):buildcache,mode=max \
		--load \
		.
	@echo "$(GREEN)多架构镜像构建完成!$(NC)"

docker-build-push: ## 构建并推送多架构优化镜像
	@echo "$(BLUE)构建并推送多架构优化镜像...$(NC)"
	@if [ -z "$(DOCKER_REGISTRY)" ] || [ "$(DOCKER_REGISTRY)" = "localhost" ]; then \
		echo "$(RED)错误: 请设置 DOCKER_REGISTRY 环境变量$(NC)"; \
		echo "$(YELLOW)示例: make docker-build-push DOCKER_REGISTRY=myregistry.com/ai$(NC)"; \
		exit 1; \
	fi
	docker buildx create --use --name aiwen-builder --driver docker-container >/dev/null 2>&1 || true
	docker buildx build \
		--file docker/Dockerfile \
		--platform linux/amd64,linux/arm64 \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(VERSION) \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(BUILD_DATE) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg UV_VERSION=$(UV_VERSION) \
		--cache-from type=registry,ref=$(DOCKER_REGISTRY)/$(DOCKER_IMAGE):buildcache \
		--cache-to type=registry,ref=$(DOCKER_REGISTRY)/$(DOCKER_IMAGE):buildcache,mode=max \
		--push \
		--provenance=false \
		.
	@echo "$(GREEN)镜像已推送: $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(VERSION)$(NC)"

docker-up: ## 使用优化的compose文件启动服务
	@echo "$(BLUE)使用优化配置启动Docker服务...$(NC)"
	cd docker && docker compose -f docker-compose.yaml up -d --build
	@echo "$(GREEN)服务已启动!$(NC)"
	@echo "$(YELLOW)查看日志: make docker-logs$(NC)"

docker-down: ## 停止优化compose服务
	@echo "$(BLUE)停止Docker服务...$(NC)"
	cd docker && docker compose -f docker-compose.yaml down
	@echo "$(GREEN)服务已停止!$(NC)"

docker-logs: ## 查看优化compose服务日志
	@echo "$(BLUE)查看Docker日志...$(NC)"
	cd docker && docker compose -f docker-compose.yaml logs -f

docker-restart: docker-down docker-up ## 重启优化服务

docker-up-multi: ## 启动多服务模式（API、Worker、MCP分离）
	@echo "$(BLUE)启动多服务模式...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml up -d --build
	@echo "$(GREEN)所有服务已启动!$(NC)"
	@echo "$(YELLOW)查看日志: make docker-logs-multi$(NC)"
	@echo "$(YELLOW)扩展Worker: make docker-scale-worker REPLICAS=3$(NC)"

docker-down-multi: ## 停止多服务模式
	@echo "$(BLUE)停止多服务模式...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml down
	@echo "$(GREEN)所有服务已停止!$(NC)"

docker-logs-multi: ## 查看多服务模式日志
	@echo "$(BLUE)查看多服务日志...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml logs -f

docker-logs-api: ## 查看API服务日志
	@echo "$(BLUE)查看API日志...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml logs -f aiwen-api

docker-logs-worker: ## 查看Worker服务日志
	@echo "$(BLUE)查看Worker日志...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml logs -f aiwen-worker

docker-logs-mcp: ## 查看MCP服务日志
	@echo "$(BLUE)查看MCP日志...$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml logs -f aiwen-mcp

docker-scale-worker: ## 扩展Worker服务（使用 REPLICAS=N）
	@echo "$(BLUE)扩展Worker服务到 $(REPLICAS) 个实例...$(NC)"
	@if [ -z "$(REPLICAS)" ]; then \
		echo "$(RED)错误: 请指定REPLICAS数量，例如: make docker-scale-worker REPLICAS=3$(NC)"; \
		exit 1; \
	fi
	cd docker && docker compose -f docker-compose.multi-service.yaml up -d --scale aiwen-worker=$(REPLICAS)
	@echo "$(GREEN)Worker已扩展到 $(REPLICAS) 个实例!$(NC)"

docker-ps-multi: ## 查看多服务状态
	@echo "$(BLUE)多服务状态:$(NC)"
	cd docker && docker compose -f docker-compose.multi-service.yaml ps

docker-restart-multi: docker-down-multi docker-up-multi ## 重启多服务模式

docker-shell: ## 进入优化容器shell
	@echo "$(BLUE)进入Docker容器shell...$(NC)"
	cd docker && docker compose -f docker-compose.yaml exec aiwen sh

docker-status: ## 查看容器状态
	@echo "$(BLUE)容器状态:$(NC)"
	cd docker && docker compose -f docker-compose.yaml ps

docker-stats: ## 查看容器资源使用情况
	@echo "$(BLUE)容器资源使用情况:$(NC)"
	docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"

docker-size: ## 查看镜像大小
	@echo "$(BLUE)镜像大小:$(NC)"
	docker images $(DOCKER_REGISTRY)/$(DOCKER_IMAGE) --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

docker-inspect-layers: ## 检查镜像层信息
	@echo "$(BLUE)镜像层信息:$(NC)"
	docker history $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) --human=true --format "table {{.CreatedBy}}\t{{.Size}}"

docker-clean: ## 清理 Docker 构建缓存
	@echo "$(BLUE)清理 Docker 构建缓存...$(NC)"
	docker builder prune -f
	@echo "$(GREEN)Docker 构建缓存清理完成!$(NC)"

docker-prune-all: ## 清理所有未使用的Docker资源
	@echo "$(YELLOW)警告: 这将清理所有未使用的Docker资源!$(NC)"
	@read -p "确定要继续吗? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "$(BLUE)清理Docker资源...$(NC)"; \
		docker system prune -a --volumes -f; \
		echo "$(GREEN)清理完成!$(NC)"; \
	fi

docker-build-dev: ## 构建开发环境镜像（包含开发依赖）
	@echo "$(BLUE)构建开发环境镜像...$(NC)"
	DOCKER_BUILDKIT=1 docker build \
		--file docker/Dockerfile \
		--target python-deps \
		--tag $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):dev \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		.
	@echo "$(GREEN)开发镜像构建完成!$(NC)"

docker-scan: ## 扫描镜像安全漏洞
	@echo "$(BLUE)扫描镜像安全漏洞...$(NC)"
	@if command -v trivy >/dev/null 2>&1; then \
		trivy image $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG); \
	else \
		echo "$(YELLOW)Trivy未安装，使用Docker扫描...$(NC)"; \
		docker scan $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG) || echo "$(RED)Docker scan 未配置$(NC)"; \
	fi

docker-info: ## 显示Docker构建信息
	@echo "$(BLUE)Docker构建信息:$(NC)"
	@echo "  镜像名称: $(DOCKER_REGISTRY)/$(DOCKER_IMAGE)"
	@echo "  标签: $(DOCKER_TAG)"
	@echo "  版本: $(VERSION)"
	@echo "  构建日期: $(BUILD_DATE)"
	@echo "  Git提交: $(GIT_COMMIT)"
	@echo "  Python版本: $(PYTHON_VERSION)"
	@echo "  UV版本: $(UV_VERSION)"
	@echo ""
	@echo "$(BLUE)BuildKit状态:$(NC)"
	@docker buildx ls || echo "$(YELLOW)BuildKit未配置$(NC)"
	@echo ""

docker-test-build: ## 测试构建（不创建标签）
	@echo "$(BLUE)测试构建...$(NC)"
	DOCKER_BUILDKIT=1 docker build \
		--file docker/Dockerfile \
		--target runtime \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg UV_VERSION=$(UV_VERSION) \
		--progress=plain \
		.
	@echo "$(GREEN)测试构建完成!$(NC)"
# 环境和配置
# ============================================================================

env-sync: ## 同步环境变量
	@echo "$(BLUE)同步环境变量...$(NC)"
	cd src && uv run sync-env

env-sync-docker: ## 同步环境变量到 docker/.env
	@echo "$(BLUE)同步环境变量到 docker/.env...$(NC)"
	uv run sync-env --docker

# ============================================================================
# 常用组合命令
# ============================================================================

ci: clean lint test-coverage ## CI 流程（清理、检查、测试）
	@echo "$(GREEN)CI 流程完成!$(NC)"

pre-commit: format lint ## 提交前检查（格式化 + 检查）
	@echo "$(GREEN)提交前检查完成!$(NC)"

quick-test: clean test-fast ## 快速测试（清理 + 快速测试）
	@echo "$(GREEN)快速测试完成!$(NC)"

full-test: clean test-coverage ## 完整测试（清理 + 覆盖率测试）
	@echo "$(GREEN)完整测试完成!$(NC)"

# ============================================================================
# 文档相关
# ============================================================================

docs-serve: ## 启动文档服务器（如果有）
	@echo "$(BLUE)启动文档服务器...$(NC)"
	@echo "$(YELLOW)文档功能尚未配置$(NC)"

# ============================================================================
# 其他工具
# ============================================================================

shell: ## 进入 Python shell
	@echo "$(BLUE)进入 Python shell...$(NC)"
	uv run python

deps-update: ## 更新依赖
	@echo "$(BLUE)更新依赖...$(NC)"
	uv lock --upgrade

deps-tree: ## 显示依赖树
	@echo "$(BLUE)显示依赖树...$(NC)"
	uv pip tree

info: ## 显示项目信息
	@echo "$(BLUE)项目信息:$(NC)"
	@echo "  项目名称: aiwen-service"
	@echo "  Python 版本: 3.12"
	@echo "  测试框架: pytest"
	@echo "  代码检查: ruff"
	@echo ""
	@echo "$(BLUE)统计信息:$(NC)"
	@echo "  Python 文件数: $$(find src/aiwen -name '*.py' | wc -l)"
	@echo "  测试文件数: $$(find tests -name 'test_*.py' | wc -l)"
	@echo "  代码行数: $$(find src/aiwen -name '*.py' -exec wc -l {} + | tail -1 | awk '{print $$1}')"
	@echo ""
