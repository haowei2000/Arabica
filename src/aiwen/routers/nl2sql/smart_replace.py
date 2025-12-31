#!/usr/bin/env python3
"""
Smart Replace Router

Provides REST API endpoint for intelligent text replacement using fuzzy matching
and dimension value substitution.

智能替换路由模块
提供基于模糊匹配和维度值替换的文本智能替换API
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body

from aiwen.schemas.common import ErrorResponse, SuccessResponse, error, success
from aiwen.schemas.nl2sql.smart_replace import (
    ReplaceDetail,
    SmartReplaceRequest,
    SmartReplaceResponse,
)
from aiwen.services.nl2sql.sentence_rewrite import smart_replace

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-smart-replace"])


@router.post(
    "/smart_replace",
    operation_id="smart_replace",
    response_model=SuccessResponse[SmartReplaceResponse],
    summary="Intelligent text replacement",
    description="""
    Intelligently replace words in text based on fuzzy matching with dimension values.

    This endpoint:
    - Analyzes input text using NLP (jieba) for word segmentation and POS tagging
    - Finds similar words from dimension value database
    - Replaces matched words with standardized dimension values
    - Returns both the replaced text and detailed replacement information

    Use cases:
    - Standardize user queries with typos or variations
    - Map colloquial terms to formal dimension values
    - Normalize text before SQL generation

    基于模糊匹配和维度值智能替换文本中的词语。
    """,
)
async def smart_replace_endpoint(
    request: Annotated[
        SmartReplaceRequest,
        Body(
            examples=[
                {
                    "text": "查询车桥机加成线的产量",
                    "threshold": 70.0,
                    "pos_mode": "noun_only",
                    "verbose": False,
                    "max_span": 5
                },
                {
                    "text": "oppO手机销量怎么样",
                    "threshold": 75.0,
                    "pos_mode": "all_noun",
                    "verbose": False,
                    "max_span": 3
                },
                {
                    "text": "笔记ben电脑的价格",
                    "threshold": 70.0,
                    "pos_mode": "noun_only",
                    "verbose": True,
                    "max_span": 5
                }
            ]
        ),
    ],
) -> SuccessResponse[SmartReplaceResponse] | ErrorResponse:
    """
    Intelligent text replacement endpoint.

    Replaces words in text using fuzzy matching against dimension values.

    Args:
        request: Smart replace request containing:
            - text: Text to process
            - threshold: Similarity threshold (0-100, default: 70)
            - pos_mode: Part-of-speech mode (default: "noun_only")
            - verbose: Enable verbose logging (default: False)
            - max_span: Maximum word combination span (default: 5)

    Returns:
        SuccessResponse containing:
            - original_text: Original input text
            - replaced_text: Text after replacements
            - replacements: List of replacement details
            - replacement_count: Number of replacements made

    智能文本替换端点。
    使用模糊匹配和维度值替换文本中的词语。

    Examples:
        Request:
            POST /nl2sql/smart_replace
            {
                "text": "查询车桥机加成线的产量",
                "threshold": 70.0,
                "pos_mode": "noun_only"
            }

        Response (Success):
            {
                "success": true,
                "message": "Smart replace completed successfully",
                "data": {
                    "original_text": "查询车桥机加成线的产量",
                    "replaced_text": "查询车桥机加线的产量",
                    "replacements": [
                        {
                            "original": "车桥机加成线",
                            "replaced": "车桥机加线",
                            "score": 85.5,
                            "pos": "n+n+v+n",
                            "span": 4
                        }
                    ],
                    "replacement_count": 1
                }
            }

        Request (with pinyin):
            POST /nl2sql/smart_replace
            {
                "text": "oppO手机销量怎么样",
                "threshold": 75.0
            }

        Response:
            {
                "success": true,
                "message": "Smart replace completed successfully",
                "data": {
                    "original_text": "oppO手机销量怎么样",
                    "replaced_text": "OPPO手机销量怎么样",
                    "replacements": [
                        {
                            "original": "oppO",
                            "replaced": "OPPO",
                            "score": 90.0,
                            "pos": "eng",
                            "span": 1
                        }
                    ],
                    "replacement_count": 1
                }
            }
    """
    logger.info(
        "Received smart replace request for text: '%s' (threshold: %.1f, mode: %s)",
        request.text,
        request.threshold,
        request.pos_mode
    )

    try:
        # Call smart_replace service
        replaced_text, matches = await smart_replace(
            text=request.text,
            threshold=request.threshold,
            pos_mode=request.pos_mode,
            verbose=request.verbose,
            max_span=request.max_span
        )

        # Convert matches to ReplaceDetail objects
        replacements = [
            ReplaceDetail(
                original=match["original"],
                replaced=match["replaced"],
                score=match["score"],
                pos=match["pos"],
                span=match["span"]
            )
            for match in matches
        ]

        # Build response
        response = SmartReplaceResponse(
            original_text=request.text,
            replaced_text=replaced_text,
            replacements=replacements,
            replacement_count=len(replacements)
        )

        logger.info(
            "Smart replace completed: %d replacements made",
            len(replacements)
        )

        if replacements:
            for detail in replacements:
                logger.debug(
                    "Replacement: '%s' -> '%s' (score: %.2f, span: %d)",
                    detail.original,
                    detail.replaced,
                    detail.score,
                    detail.span
                )

        return success(data=response, message="Smart replace completed successfully")

    except Exception as e:
        logger.exception("Error during smart replace")
        return error(
            code=500,
            message="Smart replace failed",
            detail=str(e)
        )
