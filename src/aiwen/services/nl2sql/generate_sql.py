import logging

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from aiwen.extensions.database import get_readonly_session
from aiwen.extensions.llm.llm import get_llm
from aiwen.schemas.nl2sql.generate_sql import SqlResponse
from aiwen.schemas.nl2sql.indicator_info import IndicatorInfoSchema
from aiwen.services.nl2sql.get_indicator_info import get_indicator_info_service
from aiwen.services.nl2sql.select_indicator import select_indicator_service

logger = logging.getLogger(__name__)


# @cache_route(expire=31536000)  # Permanent caching (1 year)
async def generate_sql(
        query: str,
        indicator_info: IndicatorInfoSchema,
        additional_restriction: str | None = None,
) -> SqlResponse:
    """Generate an SQL statement based on a natural language query and indicator information.

    根据自然语言查询和指标信息生成SQL语句。

    Args:
        query (str): Natural language query describing the desired data.
                     描述所需数据的自然语言查询。
        indicator_info (IndicatorInfoSchema): Information about the indicator to base the SQL on.
                                              用于生成SQL的指标信息。
        additional_restriction (str, optional): Additional restrictions to apply to the SQL query.
                                                要应用于SQL查询的额外限制条件。
    Returns:
        SqlResponse: Generated SQL statement and related information.
                     生成的SQL语句
    """
    # 构建更清晰的提示词
    sys_prompt = "你是一个SQL语句生成助手，能根据用户查询的问题生成SQL语句，可以参考用户提供的SQL示例，可选维度，和建表语句等信息"
    human_prompt = (
        f"查询问题: {query}\n\n"
        f"SQL示例: {indicator_info.sql_template}\n\n"
        f"参考维度: {indicator_info.dimensions}\n"
        f"相关表信息: {indicator_info.related_tables}\n\n"
        f"额外注意事项: {additional_restriction}\n\n"
        f"请仔细分析用户问题，并返回适用的SQL语句，以及所选维度的名称列表。特别注意处理类似“各个”、“分别”等分组要求。\n"
        f"严格按照用户要求添加分组条件。若用户没有要求按时间分组，不要随意添加时间分组。\n"
        f"确保SQL语句中的所有GROUP BY条件都在SELECT中，没有的话添加并选择合适的中文名称，以便准确区分每条数据所属分组。\n\n"
        f"请注意检查groupby中的字段是否都包含在select中，如果没有，请添加进去。\n\n"
        f"示例：\n"
        f"查询问题：查询某个产品在不同地区的销售数量和销售额。\n"
        f"SQL示例：SELECT product_id, region, SUM(sales_count), SUM(sales_amount) FROM sales_data WHERE product_id = ? GROUP BY region;\n"
        f"参考维度：region, product_id\n"
        f"相关表信息：sales_data (product_id, region, sales_count, sales_amount)\n"
        f"SQL语句：\n"
        f"SELECT product_id, region, SUM(sales_count), SUM(sales_amount) FROM sales_data WHERE product_id = '123' GROUP BY region;\n"
    )

    # 初始化 LLM 和 agent
    llm = get_llm("qwen-plus-latest", "tongyi")
    agent = create_agent(model=llm, response_format=SqlResponse.model_json_schema())

    answer = await agent.ainvoke(
        {"messages": [SystemMessage(sys_prompt), HumanMessage(human_prompt)]}
    )  # type: ignore
    struct_answer = answer["structured_response"]
    try:
        sql_response = SqlResponse.model_validate(struct_answer)
    except Exception as e:
        logger.error(
            "Struct output failed. Raw output=%s, error=%s",
            struct_answer,
            e,
            exc_info=True,
        )
        raise
    return sql_response


if __name__ == "__main__":
    import asyncio

    load_dotenv()
    query = "查询11月份累计报废率"


    async def main():
        async with get_readonly_session("mes") as db:
            indicator = await select_indicator_service(query, db)
            indicator_info = await get_indicator_info_service(
                indicator.indicator_name, db
            )
            sql = await generate_sql(query, indicator_info)
            try:
                result = await db.execute(text(sql.sql))
            except Exception as e:
                print(f"SQL执行错误: {e}")
                new_sql = await generate_sql(
                    query, indicator_info, f"修正上述SQL错误{e}"
                )
                result = await db.execute(text(new_sql.sql))
            print(result.fetchall())


    asyncio.run(main())
