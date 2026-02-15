# Tool Tag Filtering Feature

## 概述

新增工具标签筛选功能，允许用户在前端页面按tag过滤工具列表。

## 功能特性

### 后端改进

1. **API参数扩展** (`/api/v1/tools`)
   - 新增 `tags` 查询参数
   - 格式: 逗号分隔的标签列表 (例: `tags=api,search`)
   - 筛选逻辑: 工具必须包含**所有**指定的标签 (AND逻辑)

2. **数据库查询优化**
   - 使用 PostgreSQL JSONB `contains` 操作符
   - 高效的标签过滤查询

### 前端改进

1. **标签筛选面板**
   - 自动提取所有工具的唯一标签
   - 标签按字母顺序排序
   - 点击标签进行筛选 (支持多选)
   - 已选标签显示勾选标记 ✓
   - "Clear All" 按钮快速清除所有选择

2. **工具卡片增强**
   - 每个工具卡片显示其标签
   - 标签采用灰色圆角样式
   - 便于快速识别工具分类

## API 使用示例

### 请求

```http
GET /api/v1/tools?tags=api,search&enabled_only=true
```

### 响应

```json
{
  "tools": [
    {
      "id": "...",
      "name": "web_search",
      "tags": ["api", "search", "web"],
      ...
    }
  ],
  "total": 1
}
```

## 前端使用示例

### 状态管理

```typescript
const [selectedTags, setSelectedTags] = useState<string[]>([]);

const { data: toolData } = useToolList({
  enabled_only: false,
  tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined
});
```

### 标签切换

```typescript
const toggleTag = (tag: string) => {
  setSelectedTags((prev) =>
    prev.includes(tag)
      ? prev.filter((t) => t !== tag)
      : [...prev, tag]
  );
};
```

## 文件修改清单

### 后端
- ✅ `src/aiwen/services/context/tools/tool_crud.py` - 添加tags参数
- ✅ `src/aiwen/routers/context/tools/tools.py` - 添加tags查询参数

### 前端
- ✅ `frontend/src/services/toolService.ts` - 添加tags类型定义
- ✅ `frontend/src/hooks/useTools.ts` - 添加tags参数支持
- ✅ `frontend/src/pages/context/ToolPage.tsx` - 实现tag筛选UI

## 测试建议

1. **创建带标签的工具**
   ```json
   {
     "name": "test_tool",
     "tags": ["api", "test", "custom"]
   }
   ```

2. **测试筛选功能**
   - 选择单个标签
   - 选择多个标签
   - 清除所有标签
   - 验证工具列表正确过滤

3. **边界情况**
   - 工具没有标签
   - 没有工具匹配选中的标签
   - 所有标签都被选中

## 未来改进建议

1. **OR逻辑筛选** - 支持"包含任一标签"的筛选模式
2. **标签管理** - 预定义标签列表和标签颜色
3. **标签统计** - 显示每个标签对应的工具数量
4. **标签搜索** - 当标签很多时支持搜索
5. **保存筛选** - 记住用户的筛选偏好
