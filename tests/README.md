# 测试文档

本文档说明了项目的测试结构、如何编写测试以及如何运行测试。

## 目录

- [测试结构](#测试结构)
- [测试工具](#测试工具)
- [编写测试](#编写测试)
- [运行测试](#运行测试)
- [测试标记](#测试标记)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

## 测试结构

```
tests/
├── conftest.py                    # 全局测试配置和 fixtures
├── README.md                      # 测试文档（本文件）
├── utils/                         # 测试工具模块
│   ├── __init__.py
│   ├── assertions.py              # 断言辅助函数
│   ├── builders.py                # 测试数据构建器
│   └── helpers.py                 # 测试辅助函数
├── test_app.py                    # 应用级测试
├── test_extensions/               # 扩展模块测试
│   └── test_database.py
├── test_services/                 # 服务层测试
│   └── test_select_indicator_service.py
├── test_nl2sql/                   # NL2SQL 功能测试
│   ├── test_select_indicator.py
│   ├── test_get_indicator_info.py
│   ├── test_generate_sql.py
│   └── test_get_graph.py
├── test_mcp/                      # MCP 路由测试
│   └── test_nl2sql_mcp.py
└── test_neo4j/                    # Neo4j 相关测试
```

## 测试工具

### Fixtures (conftest.py)

项目提供了丰富的 fixtures 来简化测试编写：

#### 数据库相关
- `mock_db_session`: 模拟数据库会话
- `mock_db_result`: 模拟数据库查询结果

#### 测试客户端
- `test_client`: FastAPI 测试客户端

#### LLM 相关
- `mock_llm`: 模拟 LLM 实例
- `mock_agent`: 模拟 Agent 实例
- `mock_get_llm`: Mock get_llm 函数
- `mock_create_agent`: Mock create_agent 函数

#### 测试数据
- `sample_indicator_names`: 示例指标名称列表
- `sample_indicator_name`: 单个示例指标名称
- `sample_query`: 示例查询字符串
- `sample_indicator_info`: 示例指标信息
- `sample_chart_data`: 示例图表数据

#### UUID
- `valid_uuid`: 有效的 UUID
- `invalid_uuid`: 无效的 UUID

### 断言函数 (assertions.py)

```python
from tests.utils.assertions import (
    assert_response_success,
    assert_response_error,
    assert_dict_contains,
    assert_sql_valid,
)

# 断言 API 响应成功
assert_response_success(response, expected_code=200)

# 断言 API 响应错误
assert_response_error(response, expected_code=400, message_contains="Invalid")

# 断言字典包含特定键值对
assert_dict_contains(actual_dict, expected_dict, ignore_keys=["created_at"])

# 断言 SQL 有效
assert_sql_valid("SELECT * FROM users WHERE id = 1")
```

### 构建器 (builders.py)

使用 Builder 模式快速创建测试数据：

```python
from tests.utils.builders import (
    IndicatorBuilder,
    ChartDataBuilder,
    SqlResponseBuilder,
    ApiResponseBuilder,
)

# 构建指标数据
indicator = (
    IndicatorBuilder()
    .with_name("设备开机率")
    .with_sql_template("SELECT * FROM equipment")
    .with_dimension("时间", "SJ", "time_field")
    .with_table("equipment", None)
    .build()
)

# 使用预设构建器
indicator = IndicatorBuilder.production_indicator().build()

# 构建图表数据
chart_data = (
    ChartDataBuilder()
    .with_title("月度产量")
    .add_data_point("一月", 100)
    .add_data_point("二月", 150)
    .build()
)

# 构建 API 响应
response = (
    ApiResponseBuilder()
    .success()
    .with_data({"id": 1, "name": "test"})
    .build()
)
```

### 辅助函数 (helpers.py)

```python
from tests.utils.helpers import (
    async_return,
    create_mock_session,
    create_mock_result,
    mock_llm_response,
    mock_agent_response,
)

# 创建异步返回值
mock_service.get_data = async_return({"id": 1})

# 创建模拟数据库会话
session = create_mock_session()

# 创建模拟查询结果
result = create_mock_result(
    rows=[("user1", 25), ("user2", 30)],
    keys=["name", "age"]
)

# 创建模拟 LLM 响应
llm = mock_llm_response("This is a test response")

# 创建模拟 Agent 响应
agent = mock_agent_response(IndicatorSelectResponseSchema(indicator_name="test"))
```

## 编写测试

### 基本测试结构

```python
import pytest
from tests.utils.assertions import assert_response_success
from tests.utils.builders import IndicatorBuilder
from tests.utils.helpers import create_mock_session

@pytest.mark.unit
class TestMyService:
    """测试 MyService"""
    
    @pytest.mark.asyncio
    async def test_something_success(self, sample_indicator_names):
        """测试成功场景"""
        # Arrange - 准备测试数据
        mock_db = create_mock_session()
        expected_result = "success"
        
        # Act - 执行被测试的代码
        result = await my_service_function(mock_db)
        
        # Assert - 验证结果
        assert result == expected_result
        mock_db.execute.assert_called_once()
```

### 使用 Fixtures

```python
@pytest.mark.asyncio
async def test_with_fixtures(
    mock_db_session,
    sample_indicator_names,
    mock_get_llm,
):
    """使用多个 fixtures"""
    # fixtures 会自动注入
    result = await service_function(
        query="test",
        db=mock_db_session
    )
    
    assert result is not None
```

### Mock 外部依赖

```python
from unittest.mock import patch

@pytest.mark.asyncio
async def test_with_mock():
    """使用 patch 进行 mock"""
    with patch('module.external_function') as mock_func:
        mock_func.return_value = "mocked value"
        
        result = await my_function()
        
        assert result == "mocked value"
        mock_func.assert_called_once()
```

### 测试异常情况

```python
@pytest.mark.asyncio
async def test_raises_exception():
    """测试异常抛出"""
    mock_db = create_mock_session()
    
    with pytest.raises(ValueError) as exc_info:
        await service_function("", mock_db)
    
    assert "不能为空" in str(exc_info.value)
```

## 运行测试

### 运行所有测试

```bash
# 使用 pytest
pytest

# 使用 uv（推荐）
uv run pytest
```

### 运行特定测试文件

```bash
pytest tests/test_services/test_select_indicator_service.py
```

### 运行特定测试类或函数

```bash
# 运行特定类
pytest tests/test_services/test_select_indicator_service.py::TestSelectIndicatorService

# 运行特定测试函数
pytest tests/test_services/test_select_indicator_service.py::TestSelectIndicatorService::test_select_indicator_success
```

### 运行特定标记的测试

```bash
# 只运行单元测试
pytest -m unit

# 只运行集成测试
pytest -m integration

# 运行慢速测试
pytest --run-slow

# 运行集成测试
pytest --run-integration

# 排除慢速测试
pytest -m "not slow"
```

### 并行运行测试

```bash
# 自动检测 CPU 核心数
pytest -n auto

# 指定进程数
pytest -n 4
```

### 查看测试覆盖率

```bash
# 生成覆盖率报告
pytest --cov=src/structure --cov-report=html

# 查看 HTML 报告
open htmlcov/index.html
```

### 详细输出

```bash
# 显示详细信息
pytest -v

# 显示打印输出
pytest -s

# 显示最详细信息
pytest -vv -s
```

### 只运行失败的测试

```bash
# 首次运行
pytest

# 只重新运行失败的测试
pytest --lf

# 先运行失败的，再运行其他的
pytest --ff
```

## 测试标记

项目使用以下测试标记：

- `@pytest.mark.unit`: 单元测试
- `@pytest.mark.integration`: 集成测试
- `@pytest.mark.e2e`: 端到端测试
- `@pytest.mark.slow`: 慢速测试
- `@pytest.mark.smoke`: 冒烟测试
- `@pytest.mark.asyncio`: 异步测试（pytest-asyncio）

### 使用标记

```python
import pytest

@pytest.mark.unit
class TestMyUnit:
    """单元测试"""
    
    @pytest.mark.asyncio
    async def test_async_function(self):
        """异步测试"""
        result = await async_function()
        assert result is not None
    
    @pytest.mark.slow
    def test_slow_operation(self):
        """慢速测试"""
        # 耗时操作...
        pass
```

## 最佳实践

### 1. 遵循 AAA 模式

```python
async def test_example():
    # Arrange - 准备
    mock_db = create_mock_session()
    expected_result = "expected"
    
    # Act - 执行
    result = await function_under_test(mock_db)
    
    # Assert - 断言
    assert result == expected_result
```

### 2. 使用描述性的测试名称

```python
# ✅ 好的命名
def test_select_indicator_returns_error_when_query_is_empty():
    pass

# ❌ 不好的命名
def test_1():
    pass
```

### 3. 一个测试只测试一件事

```python
# ✅ 好的做法
async def test_returns_success_status():
    result = await function()
    assert result.status == "success"

async def test_returns_correct_data():
    result = await function()
    assert result.data == expected_data

# ❌ 不好的做法
async def test_everything():
    result = await function()
    assert result.status == "success"
    assert result.data == expected_data
    assert result.timestamp is not None
    # ... 太多断言
```

### 4. 使用工具函数和 Builders

```python
# ✅ 使用 Builder
indicator = IndicatorBuilder.production_indicator().build()

# ❌ 手动构建
indicator = {
    "name": "产量统计",
    "sql_template": "SELECT ...",
    "dimensions": [...],
    # ... 很多代码
}
```

### 5. 适当使用 Mock

```python
# ✅ Mock 外部依赖
with patch('module.external_api') as mock_api:
    mock_api.return_value = "mocked"
    result = await function()

# ❌ 不要 Mock 被测试的函数本身
with patch('module.function_under_test') as mock_func:
    mock_func.return_value = "result"
    result = await function_under_test()  # 这没有意义
```

### 6. 清理测试数据

```python
@pytest.fixture
def temp_file():
    """创建临时文件并在测试后清理"""
    filepath = create_temp_file("test content")
    yield filepath
    # 清理
    if os.path.exists(filepath):
        os.remove(filepath)
```

### 7. 使用参数化测试

```python
@pytest.mark.parametrize("query,expected", [
    ("产量", "产量统计"),
    ("设备", "设备开机率（生产）"),
    ("报废率", "累计报废率"),
])
async def test_select_indicator_variations(query, expected):
    """测试多种查询输入"""
    result = await select_indicator(query)
    assert result.indicator_name == expected
```

## 常见问题

### Q: 如何测试异步函数？

A: 使用 `@pytest.mark.asyncio` 装饰器：

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None
```

### Q: 如何 Mock 数据库查询？

A: 使用测试工具提供的辅助函数：

```python
from tests.utils.helpers import create_mock_session, create_mock_result

mock_db = create_mock_session()
mock_result = create_mock_result(
    rows=[("data1",), ("data2",)],
    keys=["column"]
)
mock_db.execute.return_value = mock_result
```

### Q: 测试覆盖率太低怎么办？

A: 
1. 运行 `pytest --cov=src/structure --cov-report=html` 查看覆盖率报告
2. 在 `htmlcov/index.html` 中查看哪些代码未被覆盖
3. 为未覆盖的代码添加测试
4. 重点测试核心业务逻辑

### Q: 测试运行太慢怎么办？

A:
1. 使用并行测试：`pytest -n auto`
2. 只运行单元测试：`pytest -m unit`
3. 使用 `@pytest.mark.slow` 标记慢速测试
4. 优化慢速测试，减少不必要的等待

### Q: 如何调试失败的测试？

A:
1. 使用 `-vv` 查看详细输出：`pytest -vv`
2. 使用 `-s` 查看打印输出：`pytest -s`
3. 使用 `--pdb` 进入调试器：`pytest --pdb`
4. 单独运行失败的测试：`pytest path/to/test.py::test_name`

### Q: 如何跳过某些测试？

A: 使用 `@pytest.mark.skip` 或 `@pytest.mark.skipif`：

```python
@pytest.mark.skip(reason="暂时跳过")
def test_something():
    pass

@pytest.mark.skipif(condition, reason="条件不满足时跳过")
def test_conditional():
    pass
```

## 参考资源

- [Pytest 官方文档](https://docs.pytest.org/)
- [Pytest-asyncio 文档](https://pytest-asyncio.readthedocs.io/)
- [FastAPI 测试文档](https://fastapi.tiangolo.com/tutorial/testing/)
- [Python Mock 对象库文档](https://docs.python.org/3/library/unittest.mock.html)

---

**最后更新时间**: 2024-01-01

如有问题或建议，请联系开发团队。