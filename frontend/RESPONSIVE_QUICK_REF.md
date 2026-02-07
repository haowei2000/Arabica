# 📱 响应式设计快速参考

快速查看所有响应式优化要点。

## 🎯 屏幕尺寸速查表

| 图标 | 设备 | 宽度 | Tailwind | 关键特性 |
|-----|------|------|----------|---------|
| 📱 | 手机 | < 640px | 默认 | 单列、大按钮、折叠菜单 |
| 📱 | 手机横屏 | 640px+ | `sm:` | 两列可选 |
| 📱 | 平板 | 768px+ | `md:` | 两列布局 |
| 💻 | 平板横屏 | 1024px+ | `lg:` | 侧边栏、3列 |
| 🖥️ | 桌面 | 1280px+ | `xl:` | 完整功能 |
| 🖥️ | 超大屏 | 1536px+ | `2xl:` | 最大宽度 |

## ⚡ 常用响应式模式

### 间距 (Spacing)
```tsx
px-4 sm:px-6 lg:px-8          // 内边距
gap-3 sm:gap-4 lg:gap-6        // 网格间距
space-y-4 sm:space-y-6         // 垂直间距
```

### 文字 (Typography)
```tsx
text-sm sm:text-base lg:text-lg     // 正文
text-xl sm:text-2xl lg:text-3xl     // 标题
text-xs sm:text-sm                  // 小字
```

### 布局 (Layout)
```tsx
flex flex-col lg:flex-row           // 堆叠→并排
grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3  // 响应式网格
w-full sm:w-auto                    // 宽度
```

### 显示/隐藏 (Visibility)
```tsx
hidden lg:block                     // 桌面显示
sm:hidden                          // 移动隐藏
hidden md:inline-flex              // 平板以上显示
```

## 🎨 各页面响应式要点

### LoginPage.v2
```tsx
✓ 表单：移动全宽 → 桌面固定宽度 (max-w-md)
✓ 图标：16×16 → 20×20 (sm:)
✓ 标题：2xl → 3xl → 4xl
✓ 内边距：p-6 → p-8 (sm:)
```

### HomePage.v2
```tsx
✓ 网格：1列 → 2列(md:) → 3列(xl:)
✓ 导航：移动隐藏次要按钮
✓ 卡片：移动全宽 → 桌面固定
✓ Header：紧凑 → 宽松
```

### ChatPage.v2
```tsx
✓ Header：极简移动 → 完整桌面
✓ 文字：隐藏标签 (hidden lg:inline)
✓ 按钮：仅图标 → 图标+文字
✓ 间距：px-3 → px-6 (sm:)
```

### AppWorkspacePage.v2
```tsx
✓ 侧边栏：覆盖层(mobile) → 固定(lg:)
✓ 宽度：全宽 → w-80 → w-96 (lg:)
✓ 切换：汉堡菜单(mobile) → 自动显示
✓ 位置：fixed → relative (lg:)
```

### WorkspaceConsole.v2
```tsx
✓ 消息：全宽 → max-w-2xl → max-w-3xl
✓ 侧边栏：堆叠 → 并排 (lg:)
✓ 输入：全宽 → 固定
✓ 标签：紧凑 → 宽松
```

## 🔧 调试技巧

### Chrome DevTools
```bash
F12 → Ctrl+Shift+M → 选择设备
```

### 快速测试尺寸
```bash
1. 320px  - iPhone SE (最小)
2. 375px  - iPhone 标准
3. 768px  - iPad 竖屏
4. 1024px - iPad 横屏
5. 1440px - 笔记本
6. 1920px - 桌面显示器
```

### 常见问题排查
```tsx
// 内容溢出？
overflow-x-auto          // 添加滚动

// 文字截断？
truncate                 // 单行截断
line-clamp-2            // 多行截断

// 布局错位？
min-w-0                 // 允许内容收缩
flex-1                  // 占据剩余空间
shrink-0                // 防止收缩
```

## 💡 最佳实践

### ✅ 推荐
```tsx
// 移动优先
<div className="w-full md:w-96">

// 渐进增强
<div className="text-sm sm:text-base lg:text-lg">

// 明确断点
<div className="hidden lg:block">
```

### ❌ 避免
```tsx
// 桌面优先
<div className="w-96 md:w-full">  // 不好

// 跳跃式间距
<div className="px-4 lg:px-20">   // 太大跳跃

// 模糊断点
<div className="sm:lg:hidden">     // 混乱
```

## 📐 触摸目标尺寸

移动端最小尺寸：**44×44px** (Apple) / **48×48px** (Google)

```tsx
// 按钮
<button className="min-h-[44px] min-w-[44px]">

// 输入框
<input className="h-11 px-3">  // 44px height

// 链接
<a className="inline-block p-3">  // 确保足够大
```

## 🎨 组件响应式示例

### Card
```tsx
<Card className="p-4 sm:p-6 lg:p-8">
  {/* 内容间距随屏幕增大 */}
</Card>
```

### Button
```tsx
<Button size="lg" className="w-full sm:w-auto">
  {/* 移动全宽，桌面自动宽度 */}
</Button>
```

### Input
```tsx
<Input className="text-base md:text-sm">
  {/* 移动大字体，桌面标准字体 */}
</Input>
```

## 🚀 性能优化

### 图片
```tsx
// 响应式图片
<img
  srcSet="small.jpg 320w, medium.jpg 768w, large.jpg 1200w"
  sizes="(max-width: 768px) 100vw, 50vw"
/>

// 或使用 CSS
<img className="w-full h-auto" />
```

### 字体
```tsx
// 避免布局偏移
className="font-sans antialiased"
```

### 动画
```tsx
// 尊重用户设置
@media (prefers-reduced-motion: reduce) {
  .animate-fade-in {
    animation: none;
  }
}
```

## 📱 移动端特别注意

### 1. 导航
- 使用汉堡菜单
- 图标优先于文字
- 大触摸区域

### 2. 表单
- 输入框全宽
- 大标签
- 清晰错误提示

### 3. 内容
- 单列布局
- 更大字号
- 充足行高

### 4. 交互
- 避免 hover 状态
- 使用 active 状态
- 快速反馈

## 🔗 相关文档

- 📘 [RESPONSIVE_DESIGN_GUIDE.md](./RESPONSIVE_DESIGN_GUIDE.md) - 完整指南
- 📗 [UI_MODERNIZATION_GUIDE.md](./UI_MODERNIZATION_GUIDE.md) - 组件指南
- 📕 [UI_UPDATES_SUMMARY.md](./UI_UPDATES_SUMMARY.md) - 更新总结

---

**提示**：按 Ctrl+F 快速搜索你需要的模式！
