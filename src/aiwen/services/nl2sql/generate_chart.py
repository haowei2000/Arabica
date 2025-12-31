from collections import Counter

import numpy as np
import plotly.graph_objects as go
from plotly.graph_objs import Figure

from aiwen.schemas.nl2sql.generate_chart import ReturnTypeEnum


async def auto_chart(
    data: list[dict],
    title: str | None = None,
    width: int = 1000,
    height: int = 600,
    show: bool = True,
    save_path: str | None = None,
    return_type: ReturnTypeEnum = ReturnTypeEnum.HTML,
) -> str | bytes | None | Figure:
    """
    一行代码自动生成图表 - 根据数据特征智能选择图表类型

    Args:
        data: 输入数据列表，每个元素是字典
        title: 图表标题（可选，不提供会自动生成）
        width: 图表宽度，默认1000
        height: 图表高度，默认600
        show: 是否显示图表，默认True
        save_path: 保存为文件的路径（可选，自动识别扩展名）
        return_type: 返回类型，'figure'(Figure对象) / 'html'(HTML字符串) / 'png'(PNG字节数据)，默认'figure'

    Returns:
        根据return_type返回不同类型：
        - 'figure': plotly Figure 对象
        - 'html': HTML字符串
        - 'png': PNG字节数据

    Examples:
        >>> # 返回Figure对象
        >>> fig = auto_chart(data, return_type='figure')

        >>> # 返回HTML字符串
        >>> html = auto_chart(data, return_type='html')

        >>> # 返回PNG字节
        >>> png_bytes = auto_chart(data, return_type='png')

        >>> # 直接保存文件（自动识别扩展名）
        >>> auto_chart(data, save_path='chart.html')
        >>> auto_chart(data, save_path='chart.png')

    Examples:
        >>> # 分类数据 -> 自动生成柱状图
        >>> data = [
        ...     {'category': 'A', 'value': 10},
        ...     {'category': 'B', 'value': 20}
        ... ]
        >>> fig = auto_chart(data)

        >>> # 时间序列 -> 自动生成折线图
        >>> data = [
        ...     {'month': 1, 'sales': 100, 'profit': 20},
        ...     {'month': 2, 'sales': 150, 'profit': 30}
        ... ]
        >>> fig = auto_chart(data, title="销售数据", save_path='chart.html')
    """

    # 数据验证
    if not data:
        raise ValueError("数据不能为空")

    # 分析数据特征
    features = _analyze_data(data)

    # 选择图表类型
    chart_type = _select_chart_type(features)

    # 打印分析结果
    print("📊 数据分析结果:")
    print(f"   - 行数: {features['total_rows']}")
    print(f"   - 列数: {len(features['columns'])}")
    print(f"   - 数值列: {features['numeric_columns']}")
    print(f"   - 分类列: {features['categorical_columns']}")
    print(f"✨ 自动选择图表: {chart_type.upper()}\n")

    # 生成图表
    if chart_type == "bar":
        fig = _generate_bar_chart(data, features, title)
    elif chart_type == "pie":
        fig = _generate_pie_chart(data, features, title)
    elif chart_type == "line":
        fig = _generate_line_chart(data, features, title)
    elif chart_type == "scatter":
        fig = _generate_scatter_chart(data, features, title)
    elif chart_type == "histogram":
        fig = _generate_histogram_chart(data, features, title)
    else:  # heatmap
        fig = _generate_heatmap_chart(data, features, title)

    # 更新布局
    fig.update_layout(width=width, height=height)

    # 处理返回类型和保存
    match return_type:
        case ReturnTypeEnum.HTML:
            return fig.to_html(include_plotlyjs="cdn")
        case ReturnTypeEnum.PNG:
            try:
                return fig.to_image(format="png")
            except Exception as e:
                print("⚠️ PNG导出需要安装kaleido: pip install kaleido")
                print(f"错误信息: {e}")
                return None


def _analyze_data(data: list[dict]) -> dict:
    """分析数据的特征"""
    features = {
        "total_rows": len(data),
        "columns": set(data[0].keys()),
        "column_types": {},
        "column_unique_counts": {},
        "numeric_columns": [],
        "categorical_columns": [],
    }

    for col in features["columns"]:
        values = [row.get(col) for row in data if col in row]
        unique_count = len({v for v in values if v is not None})
        features["column_unique_counts"][col] = unique_count

        # 判断数据类型
        types = [type(v).__name__ for v in values if v is not None]
        if not types:
            continue

        most_common_type = Counter(types).most_common(1)[0][0]
        features["column_types"][col] = most_common_type

        if most_common_type == "str":
            features["categorical_columns"].append(col)
        elif most_common_type in ["int", "float"]:
            features["numeric_columns"].append(col)

    return features


def _select_chart_type(features: dict) -> str:
    """根据数据特征选择图表类型"""
    num_cols = len(features["numeric_columns"])
    cat_cols = len(features["categorical_columns"])
    total_cols = len(features["columns"])
    total_rows = features["total_rows"]

    # 规则1: 2列数据，1个分类+1个数值 -> 柱状图/饼图
    if total_cols == 2 and num_cols == 1 and cat_cols == 1:
        max_categories = features["column_unique_counts"][
            features["categorical_columns"][0]
        ]
        if max_categories <= 5:
            return "pie"
        if max_categories <= 20:
            return "bar"

    # 规则2: 分类列+1-2个数值列 -> 柱状图
    if cat_cols >= 1 and num_cols >= 1:
        max_categories = max(
            [
                features["column_unique_counts"].get(col, 0)
                for col in features["categorical_columns"]
            ],
            default=0,
        )
        if max_categories <= 10 and num_cols <= 2:
            return "bar"
        if max_categories <= 5 and num_cols == 1:
            return "pie"

    # 规则3: 多个数值列，行数多 -> 折线图
    if num_cols >= 2 and total_rows > 5:
        return "line"

    # 规则4: 2个数值列 -> 散点图
    if num_cols == 2 and total_rows > 10:
        return "scatter"

    # 规则5: 1个数值列，行数多 -> 直方图
    if num_cols == 1 and total_rows > 20:
        return "histogram"

    # 规则6: 多个分类列 -> 热力图
    if cat_cols >= 2 and num_cols >= 1:
        return "heatmap"

    # 默认
    return "bar"


def _generate_bar_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成柱状图"""
    cat_col = features["categorical_columns"][0]
    num_col = features["numeric_columns"][0]

    categories = [row[cat_col] for row in data]
    values = [row[num_col] for row in data]

    if not title:
        title = f"{cat_col} 分析"

    fig = go.Figure(
        data=[
            go.Bar(
                x=categories,
                y=values,
                text=values,
                textposition="auto",
                marker={
                    "color": values,
                    "colorscale": "Blues",
                    "line": {"color": "darkblue", "width": 2},
                },
                hovertemplate="<b>%{x}</b><br>" + num_col + ": %{y:.2f}<extra></extra>",
            )
        ]
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        xaxis_title=cat_col,
        yaxis_title=num_col,
        template="plotly_white",
        hovermode="x unified",
    )

    return fig


def _generate_pie_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成饼图"""
    cat_col = features["categorical_columns"][0]
    num_col = features["numeric_columns"][0]

    categories = [row[cat_col] for row in data]
    values = [row[num_col] for row in data]

    if not title:
        title = f"{cat_col} 分布"

    fig = go.Figure(
        data=[
            go.Pie(
                labels=categories,
                values=values,
                textposition="inside",
                textinfo="label+percent",
                hovertemplate="<b>%{label}</b><br>数值: %{value}<br>占比: %{percent}<extra></extra>",
            )
        ]
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        template="plotly_white",
    )

    return fig


def _generate_line_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成折线图"""
    num_cols = features["numeric_columns"][:3]

    if not title:
        title = "数据趋势"

    fig = go.Figure()
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]

    for idx, col in enumerate(num_cols):
        values = [row.get(col, 0) for row in data]
        fig.add_trace(
            go.Scatter(
                y=values,
                name=col,
                mode="lines+markers",
                line={"color": colors[idx % len(colors)], "width": 3},
                marker={"size": 8},
                hovertemplate="<b>" + col + "</b><br>值: %{y:.2f}<extra></extra>",
            )
        )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        xaxis_title="序号",
        yaxis_title="数值",
        template="plotly_white",
        hovermode="x unified",
    )

    return fig


def _generate_scatter_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成散点图"""
    num_cols = features["numeric_columns"][:2]

    x_data = [row.get(num_cols[0], 0) for row in data]
    y_data = [row.get(num_cols[1], 0) for row in data]

    if not title:
        title = f"{num_cols[0]} vs {num_cols[1]}"

    fig = go.Figure(
        data=[
            go.Scatter(
                x=x_data,
                y=y_data,
                mode="markers",
                marker={
                    "size": 10,
                    "color": y_data,
                    "colorscale": "Viridis",
                    "showscale": True,
                    "line": {"width": 2, "color": "white"},
                },
                hovertemplate="<b>"
                + num_cols[0]
                + "</b>: %{x:.2f}<br>"
                + num_cols[1]
                + ": %{y:.2f}<extra></extra>",
            )
        ]
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        xaxis_title=num_cols[0],
        yaxis_title=num_cols[1],
        template="plotly_white",
        hovermode="closest",
    )

    return fig


def _generate_histogram_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成直方图"""
    num_col = features["numeric_columns"][0]
    values = [row[num_col] for row in data]

    if not title:
        title = f"{num_col} 分布"

    fig = go.Figure(
        data=[
            go.Histogram(
                x=values,
                nbinsx=30,
                marker={
                    "color": "steelblue",
                    "line": {"color": "darkblue", "width": 1},
                },
                hovertemplate="<b>区间</b><br>%{x:.2f}<br>频数: %{y}<extra></extra>",
            )
        ]
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        xaxis_title=num_col,
        yaxis_title="频数",
        template="plotly_white",
    )

    return fig


def _generate_heatmap_chart(
    data: list[dict], features: dict, title: str | None
) -> go.Figure:
    """生成热力图"""
    cat1 = features["categorical_columns"][0]
    cat2 = (
        features["categorical_columns"][1]
        if len(features["categorical_columns"]) > 1
        else None
    )
    num_col = features["numeric_columns"][0]

    # 构建矩阵
    matrix_dict = {}
    for row in data:
        key1 = row[cat1]
        key2 = row[cat2] if cat2 else 0
        value = row[num_col]

        if key1 not in matrix_dict:
            matrix_dict[key1] = {}
        matrix_dict[key1][key2] = value

    rows = sorted(matrix_dict.keys())
    cols = sorted({col for row_dict in matrix_dict.values() for col in row_dict})
    z = [[matrix_dict.get(row, {}).get(col, 0) for col in cols] for row in rows]

    if not title:
        title = f"{cat1} vs {cat2 or '类别'} - {num_col}"

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=cols,
            y=rows,
            colorscale="YlOrRd",
            hovertemplate="%{y} - %{x}: %{z:.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 20, "color": "darkblue"}},
        xaxis_title=cat2 or "类别2",
        yaxis_title=cat1,
        template="plotly_white",
    )

    return fig


# ==================== 使用示例 ====================
if __name__ == "__main__":
    print("🎨 自动图表生成器 - 使用示例\n")

    # 示例1: 返回Figure对象
    print("示例1️⃣: 返回Figure对象 (默认)")
    data1 = [
        {"水果": "苹果", "销售量": 45},
        {"水果": "香蕉", "销售量": 38},
        {"水果": "橙子", "销售量": 52},
        {"水果": "葡萄", "销售量": 35},
        {"水果": "西瓜", "销售量": 60},
    ]
    fig1 = auto_chart(data1, title="水果销售统计", return_type="figure")
    print()

    # 示例2: 返回HTML字符串
    print("示例2️⃣: 返回HTML字符串")
    html_str = auto_chart(data1, title="水果销售统计", return_type="html", show=False)
    print(f"HTML字符串长度: {len(html_str)} 字符")
    with open("chart_html_string.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("已将HTML字符串保存到 chart_html_string.html\n")

    # 示例3: 返回PNG字节
    print("示例3️⃣: 返回PNG字节")
    png_bytes = auto_chart(data1, title="水果销售统计", return_type="png", show=False)
    if png_bytes:
        print(f"PNG字节大小: {len(png_bytes)} bytes")
        with open("chart_from_bytes.png", "wb") as f:
            f.write(png_bytes)
        print("已将PNG字节保存到 chart_from_bytes.png\n")

    # 示例4: 直接保存HTML文件
    print("示例4️⃣: 直接保存HTML文件")
    auto_chart(data1, title="水果销售统计", save_path="chart1.html", show=False)
    print()

    # 示例5: 直接保存PNG文件
    print("示例5️⃣: 直接保存PNG文件")
    auto_chart(data1, title="水果销售统计", save_path="chart1.png", show=False)
    print()

    # 示例6: 折线图
    print("示例6️⃣: 多个数值 -> 折线图 -> 保存为HTML")
    data2 = [
        {"月": 1, "产品A": 10, "产品B": 8},
        {"月": 2, "产品A": 12, "产品B": 10},
        {"月": 3, "产品A": 15, "产品B": 12},
        {"月": 4, "产品A": 18, "产品B": 15},
        {"月": 5, "产品A": 20, "产品B": 18},
        {"月": 6, "产品A": 22, "产品B": 20},
    ]
    auto_chart(data2, title="销售趋势对比", save_path="chart2.html", show=False)
    print()

    # 示例7: 散点图
    print("示例7️⃣: 两个数值 -> 散点图 -> 返回HTML")
    data3 = [
        {"X": 10 + np.random.randn(), "Y": 20 + 2 * np.random.randn()}
        for _ in range(50)
    ]
    html_scatter = auto_chart(data3, title="相关性分析", return_type="html", show=False)
    print(f"散点图HTML长度: {len(html_scatter)} 字符\n")

    # 示例8: 直方图
    print("示例8️⃣: 单个数值 -> 直方图 -> 返回PNG")
    data4 = [{"成绩": score} for score in np.random.normal(75, 10, 100)]
    png_hist = auto_chart(data4, title="成绩分布", return_type="png", show=False)
    if png_hist:
        print(f"直方图PNG大小: {len(png_hist)} bytes\n")

    # 示例9: 饼图
    print("示例9️⃣: 少量分类 -> 饼图")
    data5 = [
        {"季度": "一季度", "收入": 30},
        {"季度": "二季度", "收入": 25},
        {"季度": "三季度", "收入": 25},
        {"季度": "四季度", "收入": 20},
    ]
    auto_chart(data5, title="季度收入分布", save_path="chart5.html", show=False)

    print("✅ 所有示例执行完成！")
