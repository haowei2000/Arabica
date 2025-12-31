"""Anomaly Detection Pydantic Schema Models"""

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JudgeMode(IntEnum):
    """Judge mode enumeration"""

    THRESHOLD = 0  # Threshold mode
    DYNAMIC = 1  # Dynamic value mode


class DynamicType(IntEnum):
    """Dynamic value type enumeration"""

    MEAN = 0  # Mean
    MEDIAN = 1  # Median


class ComparisonOperator(IntEnum):
    """Comparison operator enumeration"""

    NOT_BELOW = 0  # Should not be below
    NOT_ABOVE = 1  # Should not be above


# ==================== Input Models ====================


class ThresholdCondition(BaseModel):
    """Threshold mode condition configuration"""

    judge_mode: JudgeMode = Field(default=JudgeMode.THRESHOLD, description="Judge mode, 0=threshold")
    upper_limit: float = Field(default=float("inf"), description="Upper limit for alerts")
    lower_limit: float = Field(default=float("-inf"), description="Lower limit for alerts")

    @field_validator("upper_limit", "lower_limit")
    @classmethod
    def validate_limits(cls, v: float) -> float:
        """Validate limits are valid numbers"""
        if v != float("inf") and v != float("-inf") and (v != v):  # Check NaN
            raise ValueError("Alert limits must be valid numbers")
        return v

    def model_post_init(self, __context: Any) -> None:
        """Validation after model initialization"""
        if self.lower_limit > self.upper_limit:
            raise ValueError(f"Lower limit ({self.lower_limit}) cannot be greater than upper limit ({self.upper_limit})")


class DynamicCondition(BaseModel):
    """Dynamic value mode condition configuration"""

    judge_mode: JudgeMode = Field(default=JudgeMode.DYNAMIC, description="Judge mode, 1=dynamic")
    dynamic_type: DynamicType = Field(default=DynamicType.MEAN, description="Dynamic value type, 0=mean, 1=median")
    comparison_operator: ComparisonOperator = Field(
        default=ComparisonOperator.NOT_BELOW, description="Comparison operator, 0=not below, 1=not above"
    )


class AnomalyDetectionRequest(BaseModel):
    """Anomaly detection request model - with explicit conditions"""

    records: list[dict[str, Any]] = Field(..., description="List of records to detect")
    condition: ThresholdCondition | DynamicCondition = Field(..., description="Detection condition configuration")

    @field_validator("records")
    @classmethod
    def validate_records(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate records list"""
        if not v:
            raise ValueError("Records list cannot be empty")
        if not all(isinstance(record, dict) for record in v):
            raise ValueError("All records must be dictionaries")
        return v


class AnomalyDetectionByIndicatorRequest(BaseModel):
    """Anomaly detection request model - load conditions by indicator name"""

    data_result: list[dict[str, Any]] = Field(..., description="Query result data to analyze")
    indicator_name: str = Field(..., description="Indicator name to load conditions for")

    @field_validator("data_result")
    @classmethod
    def validate_data_result(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate data result list"""
        if not v:
            raise ValueError("Data result list cannot be empty")
        if not all(isinstance(record, dict) for record in v):
            raise ValueError("All records must be dictionaries")
        return v


# ==================== Output Models ====================


class AnomalyReason(BaseModel):
    """Anomaly reason details"""

    anomaly_value: float = Field(..., description="Anomaly value", alias="异常值")
    reason: str = Field(..., description="Reason for anomaly", alias="原因")
    location: str = Field(..., description="Location of anomaly data", alias="位置")

    # Threshold mode specific fields
    upper_limit: float | None = Field(None, description="Upper alert limit", alias="上限")
    lower_limit: float | None = Field(None, description="Lower alert limit", alias="下限")
    mean_deviation: str | None = Field(None, description="Deviation from column mean", alias="与列平均差")

    # Dynamic mode specific fields
    mean: str | None = Field(None, description="Column mean", alias="均值")
    median: str | None = Field(None, description="Column median", alias="中位数")
    std_times: str | None = Field(None, description="Standard deviation times", alias="标准差倍数")

    # Common fields
    deviation: str | None = Field(None, description="Deviation value", alias="偏差")
    deviation_rate: str | None = Field(None, description="Deviation percentage", alias="偏差率")

    class Config:
        extra = "allow"  # Allow extra fields
        populate_by_name = True  # Allow using both alias and field name


class AnomalyDetectionResponse(BaseModel):
    """Anomaly detection response model"""

    result: str = Field(..., description="Detection result (normal/detected/error)")
    anomalies: list[str] = Field(default_factory=list, description="Anomaly list, format: column:value")
    reasons: list[AnomalyReason | dict] = Field(default_factory=list, description="List of anomaly reason details")
    count: int = Field(..., description="Number of anomalies, -1 indicates detection failure")

    @field_validator("count")
    @classmethod
    def validate_count(cls, v: int) -> int:
        """Validate count"""
        if v < -1:
            raise ValueError("Anomaly count cannot be less than -1")
        return v


class ErrorDetail(BaseModel):
    """Error detail"""

    error: str = Field(..., description="Error message")


# ==================== Statistics Models ====================


class ColumnStatistics(BaseModel):
    """Column statistics"""

    mean: float = Field(..., description="Mean value")
    median: float = Field(..., description="Median value")
    std: float = Field(..., description="Standard deviation")


class NumericColumnData(BaseModel):
    """Numeric column data"""

    column_name: str = Field(..., description="Column name")
    values: list[float] = Field(..., description="List of values")
    statistics: ColumnStatistics | None = Field(None, description="Statistics info")


# ==================== Simple Anomaly Detection Models ====================


class SimpleAnomalyDetectionRequest(BaseModel):
    """Simple anomaly detection request (original anomaly_detection function)"""

    data: list[dict[str, Any]] = Field(..., description="Data list to analyze")
    indicator_name: str = Field(..., description="Indicator name")

    @field_validator("data")
    @classmethod
    def validate_data(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate data list"""
        if not isinstance(v, list):
            raise ValueError("Data must be a list")
        return v


class SimpleAnomaly(BaseModel):
    """Simple anomaly record"""

    indicator: str = Field(..., description="Indicator name")
    row_index: int | None = Field(None, description="Row index")
    reason: str = Field(..., description="Reason for anomaly")


class SimpleAnomalyDetectionResponse(BaseModel):
    """Simple anomaly detection response"""

    anomalies: list[SimpleAnomaly] = Field(default_factory=list, description="Anomaly list")


# ==================== Helper Models ====================


class AnomalyRuleResponse(BaseModel):
    """Anomaly rule response"""

    rule: str = Field(..., description="Anomaly detection rule")
    indicator_name: str = Field(..., description="Indicator name")