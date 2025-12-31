#!/usr/bin/env python3
"""
Smart Replace Schema

Pydantic models for smart_replace API request and response.

智能替换 Schema
"""

from pydantic import BaseModel, Field


class ReplaceDetail(BaseModel):
    """
    Single replacement detail.

    单次替换详情
    """
    original: str = Field(..., description="Original text")
    replaced: str = Field(..., description="Replaced text")
    score: float = Field(..., description="Similarity score (0-100)")
    pos: str = Field(..., description="Part-of-speech tags")
    span: int = Field(..., description="Word span (number of words replaced)")

    class Config:
        json_schema_extra = {
            "example": {
                "original": "车桥机加成线",
                "replaced": "车桥机加线",
                "score": 85.5,
                "pos": "n+n+v+n",
                "span": 4
            }
        }


class SmartReplaceRequest(BaseModel):
    """
    Smart replace request schema.

    智能替换请求 Schema
    """
    text: str = Field(..., description="Text to process", min_length=1)
    threshold: float = Field(
        default=70.0,
        description="Similarity threshold (0-100)",
        ge=0,
        le=100
    )
    pos_mode: str = Field(
        default="noun_only",
        description="Part-of-speech mode: 'noun_only', 'entity_only', or 'all_noun'"
    )
    verbose: bool = Field(
        default=False,
        description="Enable verbose output for debugging"
    )
    max_span: int = Field(
        default=5,
        description="Maximum word span for combination",
        ge=1,
        le=10
    )

    class Config:
        json_schema_extra = {
            "example": {
                "text": "查询车桥机加成线的产量",
                "threshold": 70.0,
                "pos_mode": "noun_only",
                "verbose": False,
                "max_span": 5
            }
        }


class SmartReplaceResponse(BaseModel):
    """
    Smart replace response schema.

    智能替换响应 Schema
    """
    original_text: str = Field(..., description="Original input text")
    replaced_text: str = Field(..., description="Text after replacement")
    replacements: list[ReplaceDetail] = Field(
        default_factory=list,
        description="List of replacement details"
    )
    replacement_count: int = Field(..., description="Number of replacements made")

    class Config:
        json_schema_extra = {
            "example": {
                "original_text": "查询车桥机加成线的产量",
                "replaced_text": "查询车桥机加总成线的产量",
                "replacements": [
                    {
                        "original": "车桥机加成线",
                        "replaced": "车桥机加总成线",
                        "score": 85.5,
                        "pos": "n+n+v+n",
                        "span": 4
                    }
                ],
                "replacement_count": 1
            }
        }
