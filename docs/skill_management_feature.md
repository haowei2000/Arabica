# Skill Management Feature

## 概述

Skill管理系统允许用户创建、编辑和管理可重用的技能集。技能以Markdown格式编写，系统会自动进行结构化处理和embedding生成。

## 功能特性

### 核心功能

1. **Markdown编辑** - 使用Markdown格式编写技能文档
2. **自动解析** - 自动解析Markdown结构（标题、代码块、列表等）
3. **结构化存储** - 提取元数据、章节信息、代码块
4. **Embedding支持** - 预留embedding生成接口（待集成）
5. **标签分类** - 支持多标签分类和筛选
6. **全文搜索** - 按名称或内容搜索技能

### 前端功能

- ✅ **技能列表** - 网格展示所有技能，支持分页
- ✅ **创建技能** - 模态对话框，包含Markdown编辑器
- ✅ **编辑技能** - 修改现有技能内容
- ✅ **删除技能** - 软删除功能
- ✅ **标签筛选** - 按标签过滤技能列表
- ✅ **搜索功能** - 全文搜索（前端hooks已实现）
- ✅ **状态显示** - 显示是否已生成embedding

## 架构设计

### 数据模型

使用现有的 `Context` 表，通过 `context_type='SKILL'` 区分：

```python
class Context(Base):
    # 基础字段
    id: UUID
    user_id: UUID
    source_id: UUID | None
    path: str | None  # 虚拟路径，用于组织

    # 类型标识
    context_type: str  # 'SKILL'

    # 渐进式披露
    glance: str | None  # 一句话摘要
    summary: str | None  # 结构化概览
    content: str  # Markdown原文

    # 分类和搜索
    tags: list[str] | None

    # Embedding支持
    embedding_384: Vector(384) | None
    embedding_768: Vector(768) | None
    embedding_1024: Vector(1024) | None
    embedding_1536: Vector(1536) | None
    embedding_3072: Vector(3072) | None

    # 元数据
    meta: dict | None  # 存储解析结果、名称等
```

### 后端组件

#### 1. Schema层 (`schemas/context/skill.py`)

```python
# 创建请求
class SkillCreate:
    name: str
    description: str | None
    content: str  # Markdown
    tags: list[str] | None

# 更新请求
class SkillUpdate:
    name: str | None
    description: str | None
    content: str | None
    tags: list[str] | None

# 响应
class SkillResponse:
    id: UUID
    name: str
    content: str
    glance: str | None
    summary: str | None
    has_embedding: bool
    created_at: datetime
```

#### 2. CRUD层 (`services/context/skill_crud.py`)

主要方法：
- `create()` - 创建技能
- `get_by_id()` - 获取单个技能
- `get_by_name()` - 按名称查找
- `update()` - 更新技能
- `delete()` - 软删除
- `list()` - 分页列表，支持标签筛选
- `search()` - 全文搜索

#### 3. 处理层 (`services/context/skill_processor.py`)

Markdown处理功能：

```python
class SkillProcessor:
    def parse_markdown(content: str) -> dict:
        """解析Markdown，提取：
        - YAML frontmatter（元数据）
        - 章节结构（标题层级）
        - 代码块（语言标记）
        - 纯文本内容
        """

    def generate_summary(parsed: dict) -> str:
        """生成结构化摘要：
        - 元数据列表
        - 章节大纲
        - 代码块统计
        """

    async def process_skill(skill_id: UUID):
        """完整处理流程：
        1. 解析Markdown
        2. 生成摘要
        3. 更新meta字段
        4. TODO: 生成embedding
        """
```

#### 4. API层 (`routers/context/skills.py`)

RESTful API端点：

| Method | Endpoint | 功能 |
|--------|----------|------|
| POST | `/api/agent/skills` | 创建技能 |
| GET | `/api/agent/skills/{id}` | 获取单个技能 |
| PUT | `/api/agent/skills/{id}` | 更新技能 |
| DELETE | `/api/agent/skills/{id}` | 删除技能 |
| GET | `/api/agent/skills` | 列表（支持分页、标签筛选） |
| GET | `/api/agent/skills/search/query` | 搜索 |
| POST | `/api/agent/skills/{id}/process` | 手动触发处理 |

### 前端组件

#### 文件结构

```
frontend/src/
├── types/skill.ts           # TypeScript类型定义
├── services/skillService.ts # API调用封装
├── hooks/useSkills.ts       # React Query hooks
└── pages/context/
    └── SkillPage.tsx        # 主页面组件
```

#### 组件功能

**SkillPage.tsx**:
- 技能网格展示
- 创建/编辑模态框
- 标签管理
- 筛选和搜索

## 使用示例

### 创建技能

```markdown
---
author: John Doe
version: 1.0
---

# Python Best Practices

## Code Style

Always follow PEP 8 guidelines:

```python
# Good
def calculate_total(items):
    return sum(item.price for item in items)

# Bad
def calculateTotal(items):
    total=0
    for i in items:total+=i.price
    return total
```

## Testing

Write unit tests for all functions...
```

### API调用示例

```bash
# 创建技能
curl -X POST /api/agent/skills \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Python Best Practices",
    "description": "Coding standards and patterns",
    "content": "# Python Best Practices\n...",
    "tags": ["python", "coding", "best-practices"]
  }'

# 列表查询（带标签筛选）
curl -X GET "/api/agent/skills?tags=python,coding&page=1&page_size=20"

# 搜索
curl -X GET "/api/agent/skills/search/query?q=best+practices"
```

## Markdown解析示例

输入Markdown:
```markdown
# API Design Guide

## REST Principles

- Use HTTP methods correctly
- Return appropriate status codes

```python
@app.get("/users/{id}")
def get_user(id: int):
    return {"id": id, "name": "John"}
```
```

解析输出:
```json
{
  "sections": [
    {
      "level": 1,
      "title": "API Design Guide",
      "content": "..."
    },
    {
      "level": 2,
      "title": "REST Principles",
      "content": "- Use HTTP methods correctly\n- Return appropriate status codes"
    }
  ],
  "code_blocks": [
    {
      "language": "python",
      "code": "@app.get(\"/users/{id}\")\ndef get_user(id: int):\n    return {\"id\": id, \"name\": \"John\"}"
    }
  ],
  "metadata": {}
}
```

生成的summary:
```
Sections:
- API Design Guide
  - REST Principles

Code blocks: 1 (python)
```

## 数据库存储

技能存储在 `context` 表中：

```sql
-- 查询所有技能
SELECT id, meta->>'name' as name, glance, summary
FROM context
WHERE context_type = 'SKILL' AND is_deleted = false;

-- 按标签筛选
SELECT * FROM context
WHERE context_type = 'SKILL'
  AND tags @> ARRAY['python']
  AND is_deleted = false;

-- 搜索
SELECT * FROM context
WHERE context_type = 'SKILL'
  AND (content ILIKE '%best practices%'
       OR meta->>'name' ILIKE '%best practices%')
  AND is_deleted = false;
```

## 文件清单

### 后端文件

- ✅ `src/structure/schemas/context/skill.py` - Pydantic schemas
- ✅ `src/structure/services/context/skill_crud.py` - CRUD operations
- ✅ `src/structure/services/context/skill_processor.py` - Markdown处理
- ✅ `src/structure/routers/context/skills.py` - API路由
- ✅ `src/structure/core/routers.py` - 路由注册（已更新）

### 前端文件

- ✅ `frontend/src/types/skill.ts` - TypeScript类型
- ✅ `frontend/src/services/skillService.ts` - API服务
- ✅ `frontend/src/hooks/useSkills.ts` - React Query hooks
- ✅ `frontend/src/pages/context/SkillPage.tsx` - 页面组件

## 待实现功能

### Embedding集成

当embedding服务可用时，在 `skill_processor.py` 中取消注释：

```python
async def process_skill(self, skill_id: UUID, embedding_model: str | None = None):
    # ... 现有代码 ...

    # 生成embedding
    from structure.services.embedding import EmbeddingService
    embedding_service = EmbeddingService()
    embeddings = await embedding_service.generate(
        skill.content,
        model=embedding_model or "default"
    )

    # 存储embedding
    skill.embedding_768 = embeddings.get('768')
    skill.embedding_1536 = embeddings.get('1536')
    # ...
```

### 未来增强

1. **Markdown预览** - 添加实时Markdown预览
2. **版本控制** - 技能的历史版本管理
3. **协作编辑** - 多用户协作功能
4. **模板系统** - 预定义技能模板
5. **导入/导出** - 支持批量导入导出Markdown文件
6. **相似度搜索** - 基于embedding的语义搜索

## 测试建议

### 单元测试

```python
async def test_create_skill():
    """测试创建技能"""
    skill = await crud.create(
        SkillCreate(
            name="Test Skill",
            content="# Test\nContent here",
            tags=["test"]
        ),
        user_id=user.id
    )
    assert skill.context_type == ContextType.SKILL
    assert skill.meta["name"] == "Test Skill"

async def test_markdown_parsing():
    """测试Markdown解析"""
    content = "# Title\n## Subtitle\n```python\ncode\n```"
    result = processor.parse_markdown(content)
    assert len(result["sections"]) == 2
    assert len(result["code_blocks"]) == 1
```

### 集成测试

```bash
# 测试完整流程
pytest tests/integration/test_skill_workflow.py -v

# 测试API端点
pytest tests/api/test_skills_api.py -v
```

## 安全考虑

1. **XSS防护** - Markdown渲染时需要sanitize
2. **权限控制** - 只能访问自己的技能
3. **内容验证** - 限制Markdown内容大小
4. **Rate Limiting** - API调用频率限制

## 性能优化

1. **分页查询** - 默认20条/页
2. **索引优化** - context_type, tags, user_id上的索引
3. **缓存策略** - Redis缓存热门技能
4. **异步处理** - Markdown解析和embedding生成异步执行

## 总结

✅ **完整实现** - 前后端完整的CRUD功能
✅ **Markdown支持** - 自动解析和结构化
✅ **标签系统** - 分类和筛选
✅ **搜索功能** - 全文搜索
✅ **Embedding预留** - 接口已预留，待集成
✅ **响应式UI** - 支持暗色模式
✅ **类型安全** - TypeScript + Pydantic

功能已完整实现，可以立即使用！
