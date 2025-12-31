from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
import re
from statistics import mean, median
from typing import Any


class CheckMode(IntEnum):
    """判断模式"""
    THRESHOLD = 0  # 阈值模式
    DYNAMIC = 1    # 动态值模式


class DynamicType(IntEnum):
    """动态值类型"""
    MEDIAN = 0
    MEAN = 1


class ComparisonOp(IntEnum):
    """比较符"""
    NOT_BELOW = 0   # 不应低于
    NOT_ABOVE = 1   # 不应高于


@dataclass
class CheckConfig:
    """检验配置"""
    mode: CheckMode
    upper_limit: float = float("inf")
    lower_limit: float = float("-inf")
    dynamic_type: DynamicType = DynamicType.MEDIAN
    comparison_op: ComparisonOp = ComparisonOp.NOT_BELOW

    @classmethod
    def from_dict(cls, check_rule: dict) -> "CheckConfig":
        """从数据库规则字典构建配置"""
        try:
            mode = CheckMode(int(check_rule.get("判断模式", 0)))

            config = cls(mode=mode)

            if mode == CheckMode.THRESHOLD:
                upper = check_rule.get("预警上限")
                lower = check_rule.get("预警下限")
                config.upper_limit = float(upper) if upper is not None else float("inf")
                config.lower_limit = float(lower) if lower is not None else float("-inf")

            elif mode == CheckMode.DYNAMIC:
                config.dynamic_type = DynamicType(int(check_rule.get("动态值类型", 0)))
                config.comparison_op = ComparisonOp(int(check_rule.get("比较符", 0)))

            return config
        except (ValueError, TypeError) as e:
            raise ValueError(f"检验规则配置错误: {e}")


class TimeParser:
    """时间解析器"""
    # 常见的时间格式模式
    TIME_PATTERNS = [
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",  # ISO 8601
        r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}",  # yyyy-MM-dd HH:mm:ss
        r"^\d{4}/\d{2}/\d{2}",                      # yyyy/MM/dd
        r"^\d{4}-\d{2}-\d{2}$",                     # yyyy-MM-dd
        r"^\d{8}$",                                  # yyyyMMdd
        r"^\d{10}$",                                 # Unix timestamp (秒)
        r"^\d{13}$",                                 # Unix timestamp (毫秒)
    ]

    TIME_KEYWORDS = {"时间", "date", "day", "dt", "年", "月", "日", "周数", "时间戳", "timestamp"}

    @classmethod
    def is_time_column(cls, key: str, value: Any = None) -> bool:
        """判断是否为时间列（基于列名和值）"""
        # 检查列名
        key_lower = key.lower()
        if any(k.lower() in key_lower for k in cls.TIME_KEYWORDS):
            return True

        # 检查值的格式
        if value is not None:
            return cls._looks_like_time(str(value))

        return False

    @classmethod
    def _looks_like_time(cls, value_str: str) -> bool:
        """检查字符串是否看起来像时间"""
        value_str = str(value_str).strip()
        return any(re.match(pattern, value_str) for pattern in cls.TIME_PATTERNS)

    @classmethod
    def parse_timestamp(cls, value: Any) -> float | None:
        """尝试解析时间戳（返回秒级Unix时间戳）"""
        if value is None:
            return None

        # 如果已经是数字，判断是否为时间戳
        try:
            num = float(value)
            # 毫秒级时间戳（13位数字，1970年后）
            if 1e12 < num < 1e13:
                return num / 1000
            # 秒级时间戳（10位数字或更小）
            if 1e9 < num < 1e10:
                return num
            # 天数偏移（Excel时间戳，1900-01-01起，通常40000-50000）
            if 30000 < num < 60000:
                return (num - 25569) * 86400  # 转换为Unix时间戳
        except (ValueError, TypeError):
            pass

        # 尝试解析常见的时间字符串
        time_formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y-%m-%d",
            "%Y%m%d",
            "%Y%m%d%H%M%S",
        ]

        value_str = str(value).strip()
        for fmt in time_formats:
            try:
                dt = datetime.strptime(value_str, fmt)
                return dt.timestamp()
            except ValueError:
                continue

        return None


class DataExtractor:
    """数据提取器"""

    @classmethod
    def extract_numeric_columns(cls, records: list[dict]) -> dict[str, list[float]]:
        """从记录列表提取所有数值列"""
        data_columns: dict[str, list[float]] = {}

        for record in records:
            if not isinstance(record, dict):
                continue

            for key, value in record.items():
                # 跳过时间列
                if TimeParser.is_time_column(key, value):
                    continue

                # 尝试转换为数值
                num_value = cls._try_float(value)
                if num_value is not None:
                    data_columns.setdefault(key, []).append(num_value)

        return data_columns

    @staticmethod
    def _try_float(value: Any) -> float | None:
        """尝试转换为浮点数"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


class ThresholdChecker:
    """阈值校验器"""

    def __init__(self, config: CheckConfig):
        self.upper_limit = config.upper_limit
        self.lower_limit = config.lower_limit

    def check(self, data_columns: dict[str, list[float]]) -> list[str]:
        """执行阈值校验"""
        violations = []

        for col, values in data_columns.items():
            for v in values:
                if not self._in_range(v):
                    violations.append(
                        f"{col}:{v} 超出阈值(下限 {self.lower_limit}, 上限 {self.upper_limit})"
                    )

        return violations

    def _in_range(self, value: float) -> bool:
        """判断值是否在范围内"""
        return self.lower_limit <= value <= self.upper_limit


class DynamicChecker:
    """动态值校验器"""

    def __init__(self, config: CheckConfig):
        self.dynamic_type = config.dynamic_type
        self.comparison_op = config.comparison_op

    def check(self, data_columns: dict[str, list[float]]) -> list[str]:
        """执行动态值校验"""
        # 检查数据充分性
        if any(len(vs) < 2 for vs in data_columns.values()):
            raise ValueError("动态值判断至少需要两条历史数据")

        violations = []

        for col, values in data_columns.items():
            reference = self._calculate_reference(values)

            for v in values:
                if self._violates(v, reference):
                    desc = "低于" if self.comparison_op == ComparisonOp.NOT_BELOW else "高于"
                    violations.append(f"{col}:{v} {desc}参考值 {reference:.4f}")

        return violations

    def _calculate_reference(self, values: list[float]) -> float:
        """计算参考值（中位数或均值）"""
        return median(values) if self.dynamic_type == DynamicType.MEDIAN else mean(values)

    def _violates(self, value: float, reference: float) -> bool:
        """判断是否违反规则"""
        if self.comparison_op == ComparisonOp.NOT_BELOW:
            return value < reference
        return value > reference


def check(check_rule: dict | None, records: list[dict]) -> dict[str, Any]:
    """
    校验逻辑主函数
    """
    if not check_rule:
        return {"result": "未找到该指标的校验规则"}

    # 解析配置
    try:
        config = CheckConfig.from_dict(check_rule)
    except ValueError as e:
        return {"result": str(e)}

    # 提取数据
    data_columns = DataExtractor.extract_numeric_columns(records)
    if not data_columns:
        return {"result": "未发现任何数值列"}

    # 执行校验
    try:
        if config.mode == CheckMode.THRESHOLD:
            violations = ThresholdChecker(config).check(data_columns)
        elif config.mode == CheckMode.DYNAMIC:
            violations = DynamicChecker(config).check(data_columns)
        else:
            return {"result": f"未知判断模式 {config.mode}"}
    except ValueError as e:
        return {"result": str(e)}

    # 返回结果
    return {
        "result": ", ".join(violations) if violations else "全部正常"
    }
