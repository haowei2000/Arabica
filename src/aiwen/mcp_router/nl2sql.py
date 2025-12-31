import logging

from fastmcp import Context, FastMCP
from sqlalchemy import text

from aiwen.extensions.database import get_readonly_session
from aiwen.schemas.nl2sql.anomaly_detection import AnomalyDetectionByIndicatorRequest
from aiwen.schemas.nl2sql.generate_chart import ChartRequestSchema, ChartResponseSchema
from aiwen.schemas.nl2sql.indicator_info import IndicatorInfoSchema
from aiwen.schemas.nl2sql.select_indicator import IndicatorSelectResponseSchema
from aiwen.services.nl2sql.anomaly.anomaly_detection import (
    detect_anomalies_by_indicator,
)
from aiwen.services.nl2sql.generate_chart import auto_chart
from aiwen.services.nl2sql.generate_sql import generate_sql as generate_sql_service
from aiwen.services.nl2sql.get_indicator_info import get_indicator_info_service
from aiwen.services.nl2sql.select_indicator import select_indicator_service

nl2sql_mcp = FastMCP("Nl2sql MCP")
logger = logging.getLogger("nl2sql_mcp")


@nl2sql_mcp.tool
async def select_indicator(query: str) -> IndicatorSelectResponseSchema:
    """Convert a natural language query into an SQL statement for indicator data retrieval.

    将自然语言查询转换为用于指标数据检索的SQL语句。

    Args:
        query (str): Natural language query describing the desired indicator data.
                     描述所需指标数据的自然语言查询。

    Returns:
        IndicatorSelectResponseSchema: Response containing the generated SQL and related information.
                                       包含生成的SQL及相关信息的响应。
    """
    async with get_readonly_session("mes") as db:
        return await select_indicator_service(query=query, db=db)


@nl2sql_mcp.tool
async def get_indicator_info(indicator_name: str) -> IndicatorInfoSchema:
    """Retrieve detailed information about a specific indicator.

    获取特定指标的详细信息。

    Args:
        indicator_name (str): The name of the indicator to retrieve information for.
                              要检索信息的指标名称。

    Returns:
        IndicatorInfoSchema: Detailed information about the indicator.
                             指标的详细信息。
    """
    async with get_readonly_session("mes") as db:
        return await get_indicator_info_service(indicator_name=indicator_name, db=db)


@nl2sql_mcp.tool
async def generate_sql(
        query: str,
        indicator_info: IndicatorInfoSchema,
        additional_restriction: str | None = None,
) -> dict:
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
        dict: Generated SQL statement and related information.
                生成的SQL语句
    """
    sql_response = await generate_sql_service(
        query=query,
        indicator_info=indicator_info,
        additional_restriction=additional_restriction,
    )
    return {"sql": sql_response.sql}


@nl2sql_mcp.tool
async def execute_sql(sql: str, ctx: Context) -> list[dict]:
    """Execute the provided SQL statement and return the results.

    执行提供的SQL语句并返回结果。

    Args:
        sql (str): The SQL statement to execute.
                   要执行的SQL语句。
        ctx (Context): MCP上下文对象
                   MCP context object.
    Returns:
        list[dict]: Query execution results as a list of dictionaries.
                    查询执行结果，以字典列表形式返回。
    """
    # 对SQL进行基本的安全检查
    if not sql or not sql.strip():
        await ctx.error("SQL语句不能为空")
        return []

    # 检查是否为SELECT语句（只允许查询操作）
    if not sql.strip().upper().startswith("SELECT"):
        await ctx.error("只允许执行SELECT查询语句")
        return []

    async with get_readonly_session("mes") as db:
        result = []
        try:
            exec_result = await db.execute(text(sql))
        except Exception as e:
            error_msg = f"SQL执行出错: {e!s}"
            logger.error(error_msg)
            await ctx.error(error_msg)
            return result

        rows = exec_result.fetchall()
        columns = exec_result.keys()
        for row in rows:
            result.append({col: row[idx] for idx, col in enumerate(columns)})

        logger.info(f"SQL执行成功，返回{len(result)}行数据")
        return result


@nl2sql_mcp.tool
async def generate_chart(
        chart_request: ChartRequestSchema,
) -> ChartResponseSchema:
    """Generate chart data based on the provided data.

    根据提供的数据生成图表数据。

    Args:
        chart_request (AutoChartRequestSchema): The data and configuration to generate chart from.
                   用于生成图表的数据和配置。
    Returns:
        AutoChartResponseSchema: Generated chart data.
                生成的图表数据
    """
    chart_result = await auto_chart(
        data=chart_request.data,
        title=chart_request.title,
        width=chart_request.width,
        height=chart_request.height,
        show=chart_request.show,
        save_path=chart_request.save_path,
        return_type=chart_request.return_type,
    )
    return ChartResponseSchema(chart=chart_result)


@nl2sql_mcp.tool
async def get_table_schema(table_name: str) -> str:
    """Retrieve a concise schema description for the specified table.

    获取指定表的简洁结构描述。

    Args:
        table_name (str): The name of the table to retrieve the schema for.
                          要检索结构的表名。
    Returns:
        str: Concise schema description including column names, types, primary keys, non-null constraints
                and comments, separated by commas.
                包括列名、类型、主键、非空约束和注释的简洁结构描述，逗号分隔。
    """
    async with get_readonly_session("mes") as db:
        from aiwen.services.nl2sql.get_indicator_info import _get_table_schema

        return await _get_table_schema(table_name, db)


@nl2sql_mcp.tool(name="指标查询工具")
async def query_indicator_data(query: str) -> dict:
    """Query indicator data based on natural language query.

    根据自然语言查询指标数据。

    Args:
        query (str): Natural language query describing the desired data.
                     描述所需数据的自然语言查询。

    Returns:
        dict: Query result including indicator name, SQL, and data.
              查询结果，包括指标名称、SQL和数据。
    """
    logger.info(f"开始处理指标查询: {query}")

    async with get_readonly_session("mes") as db:
        try:
            # 1. 选择指标
            indicator = await select_indicator_service(query, db)
            logger.info(f"选定指标: {indicator.indicator_name}")

            # 2. 获取指标信息
            indicator_info = await get_indicator_info_service(
                indicator.indicator_name, db
            )
            logger.debug("获取指标信息成功")

            # 3. 生成SQL
            sql_response = await generate_sql_service(query, indicator_info)
            logger.info(f"生成SQL: {sql_response.sql}")

            # 4. 执行SQL并获取结果
            exec_result = await db.execute(text(sql_response.sql))
            data = exec_result.fetchall()

            # 5. 将结果转换为字典列表
            columns = exec_result.keys()
            formatted_data = []
            for row in data:
                formatted_data.append(
                    {col: row[idx] for idx, col in enumerate(columns)}
                )

            logger.info(f"查询完成，返回{len(formatted_data)}行数据")
            return {
                "data": formatted_data,
                "indicator": indicator.indicator_name,
            }
        except Exception as e:
            logger.error(f"指标查询过程中发生错误: {e!s}", exc_info=True)
            # 尝试修复SQL并重新执行
            try:
                logger.info("尝试修复SQL并重新执行")
                indicator = await select_indicator_service(query, db)
                indicator_info = await get_indicator_info_service(
                    indicator.indicator_name, db
                )
                new_sql_response = await generate_sql_service(
                    query, indicator_info, additional_restriction="避免错误:" + str(e)
                )
                exec_result = await db.execute(text(new_sql_response.sql))
                data = exec_result.fetchall()

                columns = exec_result.keys()
                formatted_data = []
                for row in data:
                    formatted_data.append(
                        {col: row[idx] for idx, col in enumerate(columns)}
                    )

                logger.info(f"修复后查询完成，返回{len(formatted_data)}行数据")
                return {
                    "data": formatted_data,
                    "indicator": indicator.indicator_name,
                }
            except Exception as repair_error:
                logger.error(f"修复SQL也失败了: {repair_error!s}", exc_info=True)
                return {
                    "indicator": getattr(indicator, "indicator_name", "unknown")
                    if "indicator" in locals()
                    else "unknown",
                    "error": str(e),
                }


@nl2sql_mcp.tool(name="指标异常检测工具")
async def detect_indicator_anomalies(
        payload: AnomalyDetectionByIndicatorRequest,
) -> dict:
    """Detect anomalies in indicator data based on predefined conditions for the given indicator.

    根据给定指标的预定义条件检测指标数据中的异常。

    Args:
        payload: Anomaly detection request containing:
            - indicator_name: Name of the indicator to detect anomalies for
            - data_result: Query results from the indicator data as a list of dictionaries

    Returns:
        dict: Anomaly detection result including result status, anomalies found, detailed reasons, and count.
              异常检测结果，包括结果状态、发现的异常、详细原因和计数。
    """
    indicator_name = payload.indicator_name
    data_result = payload.data_result
    logger.info(
        "Received anomaly detection request for indicator: %s with %d records",
        indicator_name,
        len(data_result),
    )

    try:
        result = await detect_anomalies_by_indicator(
            data_result, indicator_name
        )

        # Check if detection was successful
        if result.get("count", -1) == -1:
            error_msg = result.get("reasons", [{}])[0].get("error", "Unknown error")
            logger.error(
                "Anomaly detection failed for indicator %s: %s",
                indicator_name,
                error_msg,
            )
            return {
                "result": "error",
                "error": result.get("result", "Detection failed"),
                "details": error_msg
            }

        logger.info(
            "Anomaly detection completed for indicator %s: %d anomalies found",
            indicator_name,
            result.get("count", 0),
        )

        return result

    except Exception as e:
        logger.exception("Unexpected error during anomaly detection")
        return {
            "result": "error",
            "error": "Anomaly detection failed",
            "details": str(e)
        }


@nl2sql_mcp.prompt
def nl2sql_prompt() -> str:
    """
    Initialize the prompt for indicator querying.
    初始化的提示词，用于指标查询。
    """
    return """
    你是一个指标查询助手，能够调用多个工具来帮助用户完成指标查询任务。
    注意：
    1. 请根据用户的问题，选择合适的工具来完成任务。
    2. 如果用户的问题涉及到具体的指标，请优先使用select_indicator工具来选择合适的指标。
    3. 如果需要获取指标的详细信息，请使用get_indicator_info工具。
    4. 如果需要生成SQL语句，请使用generate_sql工具
    5. 如果需要执行SQL语句，请使用execute_sql工具。
    6. 如果需要生成图表，请使用generate_chart工具，默认生成图表。
    7. 如果SQL执行结果为空，请调用get_table_schema工具获取表结构信息重新生成SQL，如果多次为空，直接返回空数据
    8. 在执行查询时，严格按照以下步骤进行：
       a) 使用select_indicator选择合适指标
       b) 使用get_indicator_info获取指标详细信息
       c) 使用generate_sql生成SQL语句
       d) 使用execute_sql执行SQL获取数据
       e) 如有必要，使用generate_chart生成图表
    """
