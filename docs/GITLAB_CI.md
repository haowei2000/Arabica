# GitLab CI/CD 配置说明文档

本文档详细说明了项目的 GitLab CI/CD 配置，包括测试流程、配置选项和使用方法。

## 📋 目录

- [概述](#概述)
- [流程架构](#流程架构)
- [配置详解](#配置详解)
- [使用指南](#使用指南)
- [常见问题](#常见问题)
- [最佳实践](#最佳实践)

---

## 🎯 概述

优化后的 GitLab CI 配置专注于测试流程，包括：

- ✅ 代码质量检查（Lint）
- ✅ 单元测试
- ✅ 快速测试
- ✅ 覆盖率测试
- ✅ 集成测试（可选）
- ✅ 测试报告生成
- ✅ GitLab Pages 部署

### 关键特性

- **自动化测试** - 在 MR 和主分支自动运行
- **并行执行** - 多个测试作业同时运行
- **智能缓存** - 加速依赖安装
- **覆盖率报告** - 可视化测试覆盖率
- **GitLab Pages** - 在线查看覆盖率报告

---

## 🏗️ 流程架构

### Pipeline 阶段

```
┌─────────────┐
│   Lint      │  代码质量检查
│ - ruff check│
│ - format    │
└─────────────┘
      ↓
┌─────────────┐
│   Test      │  测试执行
│ - unit      │  (并行运行)
│ - fast      │
│ - coverage  │
│ - integration│
└─────────────┘
      ↓
┌─────────────┐
│   Report    │  报告生成
│ - summary   │
│ - pages     │
└─────────────┘
```

### 触发条件

| 触发源 | Lint | Test | Integration | Report |
|--------|------|------|-------------|--------|
| Merge Request | ✅ | ✅ | 手动 | ✅ |
| main 分支 | ✅ | ✅ | ✅ | ✅ |
| develop 分支 | ✅ | ✅ | ✅ | ✅ |
| 其他分支 | ❌ | ❌ | ❌ | ❌ |

---

## ⚙️ 配置详解

### 变量配置

```yaml
variables:
  PYTHON_VERSION: "3.12"              # Python 版本
  UV_CACHE_DIR: ".uv-cache"           # UV 缓存目录
  COVERAGE_THRESHOLD: "60"            # 覆盖率阈值
  PYTEST_ARGS: "--junitxml=..."      # pytest 参数
```

**可自定义的变量：**

- `PYTHON_VERSION`: 修改 Python 版本
- `COVERAGE_THRESHOLD`: 调整覆盖率要求（默认 60%）
- `PYTEST_ARGS`: 自定义 pytest 参数

### 缓存策略

使用智能缓存加速构建：

```yaml
cache:
  key:
    files:
      - uv.lock          # 依赖锁定文件
      - pyproject.toml   # 项目配置
    prefix: ${CI_COMMIT_REF_SLUG}
  paths:
    - .uv-cache/
    - .pip-cache/
    - .venv/
```

**缓存特点：**
- 基于依赖文件生成唯一 key
- 分支隔离
- 自动更新

### 作业详解

#### 1. lint:ruff-check

**功能**: 代码风格检查

```yaml
lint:ruff-check:
  stage: lint
  script:
    - uv run ruff check src/structure tests
```

**作用**:
- 检查代码规范
- 发现潜在问题
- 统一代码风格

#### 2. lint:ruff-format

**功能**: 代码格式检查

```yaml
lint:ruff-format:
  stage: lint
  script:
    - uv run ruff format --check src/structure tests
```

**作用**:
- 检查代码格式
- 确保一致性

#### 3. test:unit

**功能**: 单元测试

```yaml
test:unit:
  stage: test
  script:
    - uv run pytest -m unit -v --tb=short
```

**特点**:
- 只运行单元测试
- 快速反馈
- 生成 JUnit 报告

#### 4. test:fast

**功能**: 快速测试（排除慢速测试）

```yaml
test:fast:
  stage: test
  script:
    - uv run pytest -m "not slow" -v --tb=short
```

**特点**:
- 跳过标记为 `@pytest.mark.slow` 的测试
- 适合快速验证

#### 5. test:coverage

**功能**: 测试覆盖率

```yaml
test:coverage:
  stage: test
  script:
    - uv run pytest --cov=src/structure --cov-report=xml
  coverage: '/(?i)total.*? (100(?:\.0+)?\%|[1-9]?\d(?:\.\d+)?\%)$/'
```

**特点**:
- 生成覆盖率报告
- GitLab 可视化
- 强制覆盖率阈值

**产物**:
- `coverage.xml` - Cobertura 格式
- `htmlcov/` - HTML 报告
- `report.xml` - JUnit 报告

#### 6. test:integration

**功能**: 集成测试（可选）

```yaml
test:integration:
  services:
    - postgres:15-alpine
    - mysql:8.0
    - redis:7-alpine
```

**特点**:
- 真实数据库环境
- 完整集成测试
- 默认手动触发

**何时运行**:
- main/develop 分支自动运行
- 其他分支手动触发

#### 7. report:summary

**功能**: 测试报告汇总

```yaml
report:summary:
  stage: report
  script:
    - echo "Test Summary"
    - echo "Coverage: ..."
```

**特点**:
- 汇总测试结果
- 显示关键指标
- 始终执行

#### 8. pages

**功能**: 部署覆盖率报告到 GitLab Pages

```yaml
pages:
  stage: report
  script:
    - cp -r htmlcov/* public/
  artifacts:
    paths:
      - public
```

**访问地址**:
```
https://<your-gitlab-domain>/<group>/<project>/-/pages
```

---

## 📖 使用指南

### 本地测试 CI 配置

在本地验证 CI 配置：

```bash
# 安装依赖
uv sync --group dev

# 运行 lint
uv run ruff check src/structure tests
uv run ruff format --check src/structure tests

# 运行测试
uv run pytest -m unit -v
uv run pytest -m "not slow" -v
uv run pytest --cov=src/structure --cov-report=html

# 查看覆盖率
open htmlcov/index.html
```

### 在 Merge Request 中

1. **创建 MR** - 自动触发 Pipeline
2. **查看结果** - 在 MR 页面查看测试状态
3. **修复问题** - 根据失败原因修复
4. **重新提交** - 自动触发新的 Pipeline

### 查看覆盖率报告

**方式 1: GitLab MR 页面**
- 打开 MR
- 查看 "Test Coverage" 徽章
- 点击查看详细报告

**方式 2: GitLab Pages**
- 访问 Settings > Pages
- 找到 Pages URL
- 打开覆盖率报告

**方式 3: 下载 Artifacts**
- 进入 Pipeline 页面
- 点击 Job
- 下载 `htmlcov` 文件夹

### 手动触发 Integration 测试

```bash
# 方式 1: GitLab UI
1. 打开 Pipeline
2. 找到 test:integration 作业
3. 点击播放按钮

# 方式 2: Push 到 main/develop
git push origin develop
```

---

## ❓ 常见问题

### Q1: Pipeline 失败，如何调试？

**A:** 按以下步骤排查：

1. **查看失败的 Job**
   ```
   Pipeline -> 点击失败的 Job -> 查看日志
   ```

2. **常见失败原因**
   - Lint 失败：代码格式问题
   - 测试失败：业务逻辑错误
   - 覆盖率不足：需要添加测试

3. **本地复现**
   ```bash
   # 复现 lint 失败
   uv run ruff check src/structure tests
   
   # 复现测试失败
   uv run pytest -v
   ```

### Q2: 如何跳过某个测试？

**A:** 使用 pytest 标记：

```python
@pytest.mark.skip(reason="暂时跳过")
def test_something():
    pass
```

或在 CI 中临时禁用：

```yaml
test:unit:
  script:
    - uv run pytest -m unit -v --ignore=tests/test_problematic.py
```

### Q3: 缓存没有生效？

**A:** 检查以下几点：

1. **Runner 配置**
   ```
   确保 GitLab Runner 启用了缓存
   ```

2. **清除缓存**
   ```
   Pipeline -> 右上角 "Clear runner caches"
   ```

3. **修改缓存 key**
   ```yaml
   cache:
     key: ${CI_COMMIT_REF_SLUG}-v2  # 修改版本号
   ```

### Q4: 如何调整覆盖率阈值？

**A:** 修改变量：

```yaml
variables:
  COVERAGE_THRESHOLD: "70"  # 提高到 70%
```

或在 `pyproject.toml` 中修改：

```toml
[tool.pytest.ini_options]
addopts = [
    "--cov-fail-under=70",  # 修改阈值
]
```

### Q5: 集成测试太慢怎么办？

**A:** 优化方案：

1. **只在必要时运行**
   ```yaml
   rules:
     - if: '$CI_COMMIT_BRANCH == "main"'
     - when: manual  # 其他情况手动触发
   ```

2. **并行执行**
   ```yaml
   test:integration:
     parallel: 2
   ```

3. **使用更快的数据库镜像**
   ```yaml
   services:
     - postgres:15-alpine  # alpine 版本更小
   ```

### Q6: 如何添加新的测试作业？

**A:** 参考现有模板：

```yaml
test:my-new-test:
  extends: .base_template
  stage: test
  script:
    - echo "Running my new test..."
    - uv run pytest -m my_marker -v
  artifacts:
    reports:
      junit: report-my-test.xml
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

---

## 🎓 最佳实践

### 1. 分支策略

**推荐的分支策略：**

```
main (生产) ──┐
              ├─ 严格测试，全部通过才能合并
              └─ 覆盖率 ≥ 60%

develop (开发) ──┐
                 ├─ 完整测试
                 └─ 可以稍微宽松

feature/* (功能) ──┐
                   ├─ 基础测试
                   └─ 快速反馈
```

### 2. 合理使用标记

```python
# 慢速测试标记
@pytest.mark.slow
def test_slow_operation():
    pass

# 集成测试标记
@pytest.mark.integration
def test_database_integration():
    pass

# 单元测试标记
@pytest.mark.unit
def test_pure_logic():
    pass
```

### 3. 控制 Pipeline 执行

**方法 1: 使用 rules**
```yaml
rules:
  - if: '$CI_COMMIT_MESSAGE =~ /\[skip-ci\]/'
    when: never
  - when: always
```

**方法 2: 使用 Git 提交消息**
```bash
# 跳过 CI
git commit -m "文档更新 [skip ci]"

# 只运行测试
git commit -m "修复 bug [test-only]"
```

### 4. 优化执行时间

**并行执行多个 Job：**
```yaml
test:unit:
  parallel: 3  # 分成 3 个并行任务
```

**跳过不必要的步骤：**
```yaml
before_script:
  - |
    if [ -d .venv ]; then
      echo "Using cached venv"
    else
      uv sync --group dev
    fi
```

### 5. 环境变量管理

**在 GitLab 中设置环境变量：**

1. 进入 Settings > CI/CD > Variables
2. 添加变量：
   - `COVERAGE_THRESHOLD` = `70`
   - `ENABLE_INTEGRATION_TESTS` = `true`
3. 在配置中使用：
   ```yaml
   script:
     - |
       if [ "$ENABLE_INTEGRATION_TESTS" = "true" ]; then
         uv run pytest --run-integration
       fi
   ```

### 6. Artifacts 管理

**保留重要的测试产物：**

```yaml
artifacts:
  when: always          # 即使失败也保留
  expire_in: 30 days    # 保留 30 天
  paths:
    - coverage.xml
    - htmlcov/
    - report.xml
  reports:
    junit: report.xml
    coverage_report:
      coverage_format: cobertura
      path: coverage.xml
```

---

## 📊 监控与指标

### Pipeline 成功率

目标：≥ 95%

**监控方法：**
- GitLab Analytics > CI/CD Analytics
- 查看 Pipeline 成功率趋势

### 测试覆盖率

目标：≥ 60%

**提升方法：**
1. 识别未覆盖的代码
2. 添加测试用例
3. 定期审查覆盖率报告

### 执行时间

目标：
- Lint: < 30s
- Unit Tests: < 1min
- Fast Tests: < 2min
- Coverage: < 3min
- Integration: < 5min

**优化方法：**
- 使用缓存
- 并行执行
- 减少依赖安装时间

---

## 🔧 故障排除

### Pipeline 卡住不动

**可能原因：**
1. Runner 不可用
2. 资源不足
3. 死锁

**解决方法：**
```bash
# 取消并重试
Cancel Pipeline -> Retry
```

### 测试随机失败

**可能原因：**
1. 测试顺序依赖
2. 共享状态
3. 异步问题

**解决方法：**
```python
# 使用 fixtures 隔离状态
@pytest.fixture(autouse=True)
def clean_state():
    # 清理
    yield
    # 恢复
```

### 覆盖率下降

**排查步骤：**
1. 对比上次通过的 commit
2. 检查新增/删除的代码
3. 查看覆盖率报告找出缺失部分

```bash
# 本地生成覆盖率报告
uv run pytest --cov=src/structure --cov-report=html
open htmlcov/index.html
```

---

## 📚 参考资源

- [GitLab CI/CD 文档](https://docs.gitlab.com/ee/ci/)
- [Pytest 文档](https://docs.pytest.org/)
- [UV 文档](https://docs.astral.sh/uv/)
- [项目测试文档](../tests/README.md)

---

## 📝 更新日志

### v2.0.0 (2024-01-01)
- ✨ 重构 CI 配置，专注于测试
- ✨ 添加多阶段测试流程
- ✨ 集成覆盖率报告
- ✨ 添加 GitLab Pages 支持
- 📝 完善文档

### v1.0.0 (2023-12-01)
- 🎉 初始版本

---

**最后更新**: 2024-01-01  
**维护者**: 开发团队

如有问题或建议，请提交 Issue 或联系团队。