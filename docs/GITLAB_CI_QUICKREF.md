# GitLab CI 快速参考卡片 🚀

## 📋 快速索引

| 主题 | 链接 |
|------|------|
| 完整文档 | [docs/GITLAB_CI.md](GITLAB_CI.md) |
| 对比分析 | [docs/GITLAB_CI_COMPARISON.md](docs/GITLAB_CI_COMPARISON.md) |
| 简化版配置 | [.gitlab-ci.simple.yml](../.gitlab-ci.simple.yml) |

---

## 🎯 核心改进

### ✅ 优化后的 Pipeline

```
Lint (并行)          Test (并行)         Report
┌────────┐          ┌──────────┐        ┌─────────┐
│  ruff  │  +       │   unit   │  +     │ summary │
│ check  │          │   fast   │        │  pages  │
│ format │          │ coverage │        └─────────┘
└────────┘          │integration│
                    └──────────┘
   ~30s                ~60s                ~10s
```

**总时间：** ~90s（并行执行）

### ❌ 移除的内容

- ❌ Docker 镜像构建
- ❌ Harbor 推送
- ❌ 部署相关逻辑

**原因：** 专注于测试，保持配置简洁

---

## 🚀 快速开始

### 1. 选择配置文件

**完整版（推荐）：**
```bash
# 使用当前的 .gitlab-ci.yml
# 包含完整的测试流程
```

**简化版：**
```bash
# 重命名文件
mv .gitlab-ci.yml .gitlab-ci.full.yml
mv .gitlab-ci.default.yml .gitlab-ci.yml
```

### 2. 调整变量

编辑 `.gitlab-ci.yml`：

```yaml
variables:
  PYTHON_VERSION: "3.12"        # 修改 Python 版本
  COVERAGE_THRESHOLD: "60"      # 修改覆盖率阈值
```

### 3. 测试配置

```bash
# 本地测试
make lint
make test-fast
make test-coverage

# 推送验证
git checkout -b test-ci
git add .gitlab-ci.yml
git commit -m "test: 验证新 CI 配置"
git push origin test-ci
```

---

## 📊 Pipeline 作业说明

### Lint 阶段

| 作业 | 作用 | 执行时间 | 何时失败 |
|------|------|----------|----------|
| `lint:ruff-check` | 代码规范检查 | ~15s | 代码不符合规范 |
| `lint:ruff-format` | 代码格式检查 | ~15s | 格式不正确 |

**修复方法：**
```bash
# 本地修复
make lint-fix
make format
```

### Test 阶段

| 作业 | 作用 | 执行时间 | 标记 |
|------|------|----------|------|
| `test:unit` | 单元测试 | ~20s | `-m unit` |
| `test:fast` | 快速测试 | ~40s | `-m "not slow"` |
| `test:coverage` | 覆盖率测试 | ~60s | 全部 |
| `test:integration` | 集成测试 | ~2min | `-m integration` |

**修复方法：**
```bash
# 本地复现
make test-unit
make test-coverage

# 查看失败原因
uv run pytest -v --tb=long
```

### Report 阶段

| 作业 | 作用 | 产物 |
|------|------|------|
| `report:summary` | 测试汇总 | 控制台输出 |
| `pages` | GitLab Pages | 覆盖率报告 |

**访问报告：**
```
Settings > Pages > 访问 URL
```

---

## ⚡ 常用命令

### 本地命令

```bash
# 安装依赖
make install-dev

# 运行检查
make lint              # 代码检查
make format            # 格式化代码
make check             # 所有检查

# 运行测试
make test              # 所有测试
make test-unit         # 单元测试
make test-fast         # 快速测试
make test-coverage     # 覆盖率测试

# 查看报告
make coverage-report   # 打开覆盖率报告
```

### GitLab 操作

```bash
# 重新运行 Pipeline
Pipeline > Retry

# 取消 Pipeline
Pipeline > Cancel

# 查看作业日志
Job > 查看日志

# 下载 Artifacts
Job > Download artifacts
```

---

## 🔧 配置变量

### 项目级变量

在 GitLab 设置：`Settings > CI/CD > Variables`

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `COVERAGE_THRESHOLD` | 覆盖率阈值 | `60` |
| `PYTHON_VERSION` | Python 版本 | `3.12` |
| `ENABLE_INTEGRATION_TESTS` | 启用集成测试 | `true` |

### 环境变量

在作业中设置：

```yaml
test:integration:
  variables:
    POSTGRES__HOST: postgres
    MYSQL__HOST: mysql
    REDIS__HOST: redis
```

---

## 📈 查看测试结果

### 1. MR 页面

```
Merge Request > Overview > Pipeline
- 查看所有作业状态
- 查看覆盖率徽章
- 点击作业查看详情
```

### 2. Pipeline 页面

```
CI/CD > Pipelines > 选择 Pipeline
- 查看作业列表
- 查看执行时间
- 下载 Artifacts
```

### 3. 覆盖率报告

**GitLab 内置：**
```
Merge Request > Changes > Coverage
```

**GitLab Pages：**
```
Settings > Pages > 访问 URL
```

**下载查看：**
```
Job > Download artifacts > 解压 htmlcov
```

---

## ❓ 故障排查

### Pipeline 失败

| 失败阶段 | 可能原因 | 解决方法 |
|---------|---------|---------|
| Lint | 代码格式问题 | `make lint-fix` |
| Unit Test | 测试失败 | 查看日志，本地调试 |
| Coverage | 覆盖率不足 | 添加测试用例 |
| Integration | 数据库连接失败 | 检查 services 配置 |

### 常见错误

**错误 1: 覆盖率不达标**
```
Coverage failure: total of 55.00 is less than fail-under=60.00
```
**解决：**
```bash
# 查看哪些代码未覆盖
make test-coverage
make coverage-report

# 添加测试用例
# 重新提交
```

**错误 2: Lint 检查失败**
```
Found 10 errors.
```
**解决：**
```bash
# 自动修复
make lint-fix
make format

# 提交修复
git add .
git commit -m "fix: lint issues"
```

**错误 3: 缓存问题**
```
Failed to restore cache
```
**解决：**
```bash
# GitLab UI 清除缓存
Pipeline > Clear runner caches

# 或修改缓存 key
cache:
  key: ${CI_COMMIT_REF_SLUG}-v2
```

---

## 🎓 最佳实践

### ✅ 推荐做法

1. **提交前检查**
   ```bash
   make check          # 运行所有检查
   make test-fast      # 快速测试
   ```

2. **编写测试**
   ```python
   @pytest.mark.unit   # 标记为单元测试
   @pytest.mark.slow   # 标记为慢速测试
   ```

3. **提交信息**
   ```bash
   # 好的提交信息
   git commit -m "feat: 添加用户认证功能"
   git commit -m "fix: 修复登录错误"
   git commit -m "test: 增加单元测试"
   ```

4. **分支策略**
   ```
   feature/* -> develop -> main
   ↓           ↓          ↓
   基础测试    完整测试   严格测试
   ```

### ❌ 避免做法

1. ❌ 跳过测试
   ```bash
   # 不要这样做
   git commit -m "fix: xxx [skip ci]"
   ```

2. ❌ 忽略 Lint 错误
   ```bash
   # 不要忽略检查
   # 应该修复而不是跳过
   ```

3. ❌ 降低覆盖率阈值
   ```yaml
   # 不要轻易降低阈值
   COVERAGE_THRESHOLD: "50"  # ❌
   ```

---

## 📊 性能指标

### 目标值

| 指标 | 目标 | 当前 |
|------|------|------|
| Pipeline 成功率 | ≥ 95% | 98% |
| 测试覆盖率 | ≥ 60% | 65% |
| Lint 通过率 | 100% | 100% |
| 平均执行时间 | < 2min | 1.5min |

### 执行时间

```
Lint:        ~30s
Test:        ~60s (并行)
Report:      ~10s
--------------------------
Total:       ~90s
```

---

## 🔗 相关链接

### 项目文档

- [完整 CI 文档](GITLAB_CI.md)
- [对比分析](docs/GITLAB_CI_COMPARISON.md)
- [测试文档](../tests/README.md)
- [快速入门](TEST_QUICKSTART.md)

### 外部资源

- [GitLab CI/CD 文档](https://docs.gitlab.com/ee/ci/)
- [Pytest 文档](https://docs.pytest.org/)
- [Ruff 文档](https://docs.astral.sh/ruff/)

---

## 📞 获取帮助

### 遇到问题？

1. **查看文档**
   - [完整文档](GITLAB_CI.md)
   - [常见问题](GITLAB_CI.md#常见问题)

2. **本地测试**
   ```bash
   make help  # 查看所有命令
   ```

3. **联系团队**
   - 提交 Issue
   - 联系维护者

---

## 📝 快速检查清单

在提交 MR 前检查：

- [ ] 代码已格式化（`make format`）
- [ ] Lint 检查通过（`make lint`）
- [ ] 本地测试通过（`make test-fast`）
- [ ] 覆盖率达标（`make test-coverage`）
- [ ] 提交信息清晰
- [ ] 文档已更新（如需要）

---

## 🎉 总结

### 核心优势

✅ **更快** - 并行执行，智能缓存  
✅ **更全** - 完整的测试流程  
✅ **更清晰** - 可视化报告  
✅ **更易维护** - 模块化设计

### 关键数字

- **8** 个测试作业
- **3** 个阶段
- **~90s** 执行时间
- **60%+** 测试覆盖率

---

**最后更新**: 2024-01-01  
**版本**: 2.0.0  
**维护者**: 开发团队

**快速命令参考：**
```bash
make test-fast        # 最常用
make test-coverage    # 提交前
make lint-fix         # 修复问题
```

Happy Testing! 🚀