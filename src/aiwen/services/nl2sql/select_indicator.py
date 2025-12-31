import logging
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_readonly_session
from aiwen.extensions.llm.llm import get_llm
from aiwen.models.mes.indicator import AiIndicatorInfo
from aiwen.schemas.nl2sql.select_indicator import IndicatorSelectResponseSchema

logger = logging.getLogger(__name__)


async def select_indicator_service(
        query: str, db: AsyncSession
) -> IndicatorSelectResponseSchema | None | Any:
    """Select an appropriate indicator based on the provided query string.

    根据输入的查询字符串，查找并返回最合适的指标。返回的指标将匹配查询中指定的条件。

    Args:
        query (str): 用于确定选择哪个指标的输入字符串。
        db (AsyncSession): 数据库会话

    Returns:
        IndicatorSelectResponseSchema: 包含最佳匹配查询条件的指标信息。

    Raises:
        ValueError: 如果查询字符串为空或没有可用指标。
    """
    if not query or not query.strip():
        raise ValueError("查询字符串不能为空")

    # 获取可用指标列表
    result = await db.execute(
        select(AiIndicatorInfo.name)
        .where(AiIndicatorInfo.is_delete == 0)
        .order_by(AiIndicatorInfo.name)  # 保证顺序一致性
    )
    allowed_indicator_names = [row[0] for row in result.fetchall()]

    if not allowed_indicator_names:
        raise ValueError("没有可用的指标")

    # 构建更清晰的提示词
    human_prompt = (
        f"请从以下指标集中选择最匹配用户问题的指标名称。如果没有合适的指标，返回 null。\n\n"
        f"用户问题：{query}\n\n"
        f"可选指标（共 {len(allowed_indicator_names)} 个）：\n"
        f"{', '.join(allowed_indicator_names)}\n\n"
        f"请仔细分析用户问题的意图，选择最相关的指标。"
        f"如果在指标库中有本身完全包含在用户问题中指标，优先选择该指标。"
        f"注意：只能从提供的指标列表中选择，不能自行创造新的指标名称。"
    )

    # 初始化 LLM 和 agent
    llm = get_llm("qwen-plus-latest", "tongyi")
    agent = create_agent(model=llm, response_format=IndicatorSelectResponseSchema)

    success = False
    # 调用 agent 获取结果
    max_retries = 3
    retries = 0

    while not success and retries < max_retries:
        try:
            answer = await agent.ainvoke({"messages": [HumanMessage(human_prompt)]})  # type: ignore
            struct_answer = answer["structured_response"]
            if struct_answer.indicator_name is None:
                success = True  # Accept null response as valid
                return struct_answer
            if struct_answer.indicator_name in allowed_indicator_names:
                logger.info(f"选择指标成功，返回指标名称：{struct_answer.indicator_name}")
                return struct_answer
            logger.error(f"选择指标时出错: 指标名称 {struct_answer.indicator_name} 不在可选列表中")
            success = False
        except Exception as e:
            retries += 1
            logger.error(f"选择指标时出错: {e}. 重试次数: {retries}")
            if retries >= max_retries:
                # 如果多次尝试都失败，返回空结果而不是无限循环
                logger.warning("选择指标时多次尝试都失败，返回空结果")
                return IndicatorSelectResponseSchema(indicator_name=None)
            continue
    return None


if __name__ == "__main__":
    import asyncio

    load_dotenv()


    async def main():
        async with get_readonly_session("mes") as db:
            result = await select_indicator_service("今天生产了多少产品", db)
            print(result)


    asyncio.run(main())
