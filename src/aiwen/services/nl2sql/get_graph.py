from collections.abc import Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.mes.indicator import AiIndicatorInfo, AiIndicatorRelation
from aiwen.schemas.nl2sql.graph import Edge, GraphResponse, Node


async def get_indicator_graph(
    session: AsyncSession,
    indicator_id: str | None,
) -> GraphResponse | None:
    """
    获取指标血缘图（支持递归子指标）

    :param session: 异步数据库会话
    :param indicator_id: 指标 GID（字符串）
    :return: 指标图谱字典，或 None（若指标不存在/已删除）
    """
    # 1. 验证主指标是否存在
    if indicator_id is not None:
        if not await _validate_indicator(session, indicator_id):
            return None

    # 2. 获取所有相关的指标关系
    relations = await _get_related_relations(session, indicator_id)

    # 3. 收集所有涉及的指标ID
    indicator_ids = _collect_indicator_ids(indicator_id, relations)

    # 4. 获取所有涉及的指标信息
    indicators = await _get_indicators(session, indicator_ids)

    # 5. 构建节点和边数据
    nodes, indicator_map = _build_nodes(indicators)
    edges = _build_edges(relations, indicator_map)

    return GraphResponse(nodes=nodes, edges=edges)


async def _validate_indicator(session: AsyncSession, indicator_id: str) -> bool:
    """验证指标是否存在且有效"""
    query = select(AiIndicatorInfo.gid).where(
        and_(
            AiIndicatorInfo.gid == indicator_id,
            AiIndicatorInfo.is_delete == 0,
            AiIndicatorInfo.is_active_raw == 0,
        )
    )
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


async def _get_related_relations(
    session: AsyncSession, indicator_id: str | None
) -> Sequence[AiIndicatorRelation]:
    """获取所有相关的指标关系"""
    if indicator_id is None:
        query = select(AiIndicatorRelation).where(
            and_(
                AiIndicatorRelation.is_delete == 0,
                AiIndicatorRelation.is_active_raw == 0,
            )
        )
    else:
        query = select(AiIndicatorRelation).where(
            and_(
                AiIndicatorRelation.is_delete == 0,
                AiIndicatorRelation.is_active_raw == 0,
                (AiIndicatorRelation.p_indicator_id == indicator_id)
                | (AiIndicatorRelation.sub_indicator_id == indicator_id),
            )
        )
    result = await session.execute(query)
    return result.scalars().all()


def _collect_indicator_ids(
    indicator_id: str, relations: Sequence[AiIndicatorRelation]
) -> set[str]:
    """收集所有涉及的指标ID"""
    indicator_ids = {indicator_id}
    for relation in relations:
        if isinstance(relation.p_indicator_id, str):
            indicator_ids.add(relation.p_indicator_id)
        if isinstance(relation.sub_indicator_id, str):
            indicator_ids.add(relation.sub_indicator_id)
    return indicator_ids


async def _get_indicators(
    session: AsyncSession, indicator_ids: set[str]
) -> Sequence[AiIndicatorInfo]:
    """获取所有涉及的指标信息"""
    query = select(AiIndicatorInfo).where(
        and_(
            AiIndicatorInfo.gid.in_(indicator_ids),
            AiIndicatorInfo.is_delete == 0,
            AiIndicatorInfo.is_active_raw == 0,
        )
    )
    result = await session.execute(query)
    return result.scalars().all()


def _build_nodes(indicators: Sequence[AiIndicatorInfo]) -> tuple:
    """构建节点数据"""
    nodes = []
    indicator_map = {}
    for indicator in indicators:
        nodes.append(
            Node(
                id=str(indicator.gid),
                name=str(indicator.name),
                type=int(indicator.type),
                code=str(indicator.gid),
                description=str(indicator.description),
            )
        )
        indicator_map[indicator.gid] = indicator
    return nodes, indicator_map


def _build_edges(relations: Sequence[AiIndicatorRelation], indicator_map: dict) -> list:
    """构建边数据"""
    edges = []
    for relation in relations:
        # 确保关系的两端都存在
        if (
            relation.p_indicator_id
            and relation.sub_indicator_id
            and relation.p_indicator_id in indicator_map
            and relation.sub_indicator_id in indicator_map
        ):
            edges.append(
                Edge(
                    source=str(relation.p_indicator_id),
                    target=str(relation.sub_indicator_id),
                    relation=None,
                )
            )
    return edges
