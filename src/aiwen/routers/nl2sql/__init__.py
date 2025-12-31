#!/usr/bin/env python3
"""
NL2SQL Router Module

Main router that aggregates all nl2sql sub-routers.
Provides endpoints for:
- Graph retrieval
- Table description
- Indicator selection and sub-indicators
- SQL generation
- Dimension retrieval
- Anomaly detection
- Smart replace (intelligent text replacement)

NL2SQL 主路由模块

作者: wanghaowei
创建日期: 2025-12-30
版本: v2.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.
"""

from fastapi import APIRouter

from . import anomaly, dimension, graph, indicator, smart_replace, sql, table

# Create main nl2sql router
router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])

# Include all sub-routers
router.include_router(graph.router)
router.include_router(table.router)
router.include_router(indicator.router)
router.include_router(sql.router)
router.include_router(dimension.router)
router.include_router(anomaly.router)
router.include_router(smart_replace.router)

__all__ = ["router"]
