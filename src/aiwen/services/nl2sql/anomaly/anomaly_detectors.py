"""异常检测策略模块"""

from typing import Any

from aiwen.schemas.nl2sql.anomaly_detection import AnomalyReason

from .anomaly_utils import calculate_statistics, format_decimal, format_percentage, format_std_times
from .anomaly_validators import (
    ValidationError,
    validate_dynamic_condition,
    validate_sufficient_data,
    validate_threshold_condition,
)


class AnomalyResult:
    """异常检测结果"""

    def __init__(self, anomaly_str: str, reason: AnomalyReason):
        self.anomaly_str = anomaly_str
        self.reason = reason


class AnomalyDetector:
    """异常检测器基类"""

    def __init__(self, records: list[dict], numeric_columns: dict[str, list[float]]):
        self.records = records
        self.numeric_columns = numeric_columns
        self.anomalies: list[str] = []
        self.reasons: list[AnomalyReason] = []

    def _create_anomaly_entry(
        self,
        column: str,
        value: float,
        index: int,
        reason: str,
        **extra_fields: Any,
    ) -> AnomalyResult:
        """
        创建异常记录条目

        Args:
            column: 列名
            value: 异常值
            index: 记录索引
            reason: 异常原因
            **extra_fields: 其他字段

        Returns:
            AnomalyResult: 异常结果对象
        """
        anomaly_str = f"{column}:{value}"

        # Use English field names (Pydantic model supports both via aliases)
        reason_data = {
            "anomaly_value": value,
            "reason": reason,
            **extra_fields,
            "location": f"Anomaly data: {self.records[index]}: {value} at position {index + 1}",
        }
        reason_model = AnomalyReason(**reason_data)

        return AnomalyResult(anomaly_str, reason_model)

    def _add_anomaly(self, result: AnomalyResult) -> None:
        """添加异常记录"""
        self.anomalies.append(result.anomaly_str)
        self.reasons.append(result.reason)

    def detect(self, condition: dict) -> tuple[list[str], list[dict]]:
        """
        执行异常检测

        Args:
            condition: 条件配置

        Returns:
            tuple[list[str], list[dict]]: (异常列表, 原因列表)
        """
        raise NotImplementedError

    def _get_results(self) -> tuple[list[str], list[dict]]:
        """
        获取检测结果，将 Pydantic 模型转换为字典

        Returns:
            tuple[list[str], list[dict]]: (异常列表, 原因字典列表)
        """
        return self.anomalies, [reason.model_dump() for reason in self.reasons]


class ThresholdDetector(AnomalyDetector):
    """阈值模式异常检测器"""

    def detect(self, condition: dict) -> tuple[list[str], list[dict]]:
        """
        阈值模式异常检测

        Args:
            condition: 条件配置

        Returns:
            tuple[list[str], list[dict]]: (异常列表, 原因列表)
        """
        try:
            lower_limit, upper_limit = validate_threshold_condition(condition)
        except ValidationError as e:
            return [], [{"error": e.message}]

        for column, values in self.numeric_columns.items():
            statistics = calculate_statistics(values)
            self._check_column_threshold(column, values, statistics, lower_limit, upper_limit)

        return self._get_results()

    def _check_column_threshold(
        self,
        column: str,
        values: list[float],
        statistics: dict[str, float],
        lower_limit: float,
        upper_limit: float,
    ) -> None:
        """检查单列的阈值异常"""
        for i, value in enumerate(values):
            if value < lower_limit:
                result = self._create_lower_threshold_anomaly(
                    column, value, i, lower_limit, statistics["mean"]
                )
                self._add_anomaly(result)
            elif value > upper_limit:
                result = self._create_upper_threshold_anomaly(
                    column, value, i, upper_limit, statistics["mean"]
                )
                self._add_anomaly(result)

    def _create_lower_threshold_anomaly(
        self,
        column: str,
        value: float,
        index: int,
        lower_limit: float,
        mean_value: float,
    ) -> AnomalyResult:
        """创建低于下限的异常记录"""
        deviation = lower_limit - value
        deviation_percent = (deviation / lower_limit * 100) if lower_limit != 0 else 0

        return self._create_anomaly_entry(
            column,
            value,
            index,
            "Below lower limit",
            lower_limit=lower_limit,
            deviation=format_decimal(deviation),
            deviation_rate=format_percentage(deviation_percent),
            mean_deviation=format_decimal(mean_value - value),
        )

    def _create_upper_threshold_anomaly(
        self,
        column: str,
        value: float,
        index: int,
        upper_limit: float,
        mean_value: float,
    ) -> AnomalyResult:
        """创建超过上限的异常记录"""
        deviation = value - upper_limit
        deviation_percent = (deviation / upper_limit * 100) if upper_limit != 0 else 0

        return self._create_anomaly_entry(
            column,
            value,
            index,
            "Above upper limit",
            upper_limit=upper_limit,
            deviation=format_decimal(deviation),
            deviation_rate=format_percentage(deviation_percent),
            mean_deviation=format_decimal(value - mean_value),
        )


class DynamicDetector(AnomalyDetector):
    """动态值模式异常检测器"""

    def detect(self, condition: dict) -> tuple[list[str], list[dict]]:
        """
        动态值模式异常检测

        Args:
            condition: 条件配置

        Returns:
            tuple[list[str], list[dict]]: (异常列表, 原因列表)
        """
        try:
            dynamic_type, comparison = validate_dynamic_condition(condition)
            validate_sufficient_data(self.numeric_columns)
        except ValidationError as e:
            return [], [{"error": e.message}]

        type_name = "median" if dynamic_type == 1 else "mean"

        for column, values in self.numeric_columns.items():
            statistics = calculate_statistics(values)
            reference = statistics["median"] if dynamic_type == 1 else statistics["mean"]

            self._check_column_dynamic(
                column, values, reference, statistics, comparison, type_name
            )

        return self._get_results()

    def _check_column_dynamic(
        self,
        column: str,
        values: list[float],
        reference: float,
        statistics: dict[str, float],
        comparison: int,
        type_name: str,
    ) -> None:
        """检查单列的动态值异常"""
        for i, value in enumerate(values):
            if comparison == 0 and value < reference:
                result = self._create_lower_dynamic_anomaly(
                    column, value, i, reference, statistics["std"], type_name
                )
                self._add_anomaly(result)
            elif comparison == 1 and value > reference:
                result = self._create_upper_dynamic_anomaly(
                    column, value, i, reference, statistics["std"], type_name
                )
                self._add_anomaly(result)

    def _create_lower_dynamic_anomaly(
        self,
        column: str,
        value: float,
        index: int,
        reference: float,
        std: float,
        type_name: str,
    ) -> AnomalyResult:
        """创建低于参考值的异常记录"""
        deviation = reference - value
        deviation_percent = (deviation / reference * 100) if reference != 0 else 0
        std_times_value = (deviation / std) if std > 0 else 0

        # Use English field names
        extra_fields = {
            "deviation": format_decimal(deviation),
            "deviation_rate": format_percentage(deviation_percent),
            "std_times": format_std_times(std_times_value),
        }

        # Add mean or median field based on type_name
        if type_name == "median" or type_name == "中位数":
            extra_fields["median"] = format_decimal(reference)
        else:
            extra_fields["mean"] = format_decimal(reference)

        return self._create_anomaly_entry(
            column,
            value,
            index,
            f"Below {type_name}",
            **extra_fields,
        )

    def _create_upper_dynamic_anomaly(
        self,
        column: str,
        value: float,
        index: int,
        reference: float,
        std: float,
        type_name: str,
    ) -> AnomalyResult:
        """创建高于参考值的异常记录"""
        deviation = value - reference
        deviation_percent = (deviation / reference * 100) if reference != 0 else 0
        std_times_value = (deviation / std) if std > 0 else 0

        # Use English field names
        extra_fields = {
            "deviation": format_decimal(deviation),
            "deviation_rate": format_percentage(deviation_percent),
            "std_times": format_std_times(std_times_value),
        }

        # Add mean or median field based on type_name
        if type_name == "median" or type_name == "中位数":
            extra_fields["median"] = format_decimal(reference)
        else:
            extra_fields["mean"] = format_decimal(reference)

        return self._create_anomaly_entry(
            column,
            value,
            index,
            f"Above {type_name}",
            **extra_fields,
        )


def create_detector(
    mode: int, records: list[dict], numeric_columns: dict[str, list[float]]
) -> AnomalyDetector:
    """
    创建异常检测器

    Args:
        mode: 检测模式 (0=阈值, 1=动态值)
        records: 记录列表
        numeric_columns: 数值列数据

    Returns:
        AnomalyDetector: 异常检测器实例

    Raises:
        ValueError: 未知的检测模式
    """
    if mode == 0:
        return ThresholdDetector(records, numeric_columns)
    if mode == 1:
        return DynamicDetector(records, numeric_columns)

    msg = f"未知判断模式 {mode}，只能是0(阈值)或1(动态值)"
    raise ValueError(msg)