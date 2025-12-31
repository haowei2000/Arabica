from sqlalchemy import text

from aiwen.extensions.database import get_readonly_session
from aiwen.services.nl2sql.dimension_registry import (
    register_dimension,
    register_values_getter,
)


@register_values_getter("产品名称")
@register_dimension("产品名称")
async def query_allowed_product_name() -> list[str]:
    async with get_readonly_session('mes') as session:
        query = text("""SELECT DISTINCT 产品名称
                        FROM pv_uex_daq_info
                        WHERE 产品名称 IS NOT NULL
                          AND 产品名称 <> '';""")
        result = await session.execute(query)
        return [row[0] for row in result]

@register_values_getter("工作中心名称")
@register_dimension("工作中心名称")
async def query_allowed_product_name() -> list[str]:
    async with get_readonly_session('mes') as session:
        query = text("""SELECT DISTINCT 工作中心名称
                        FROM pv_uex_daq_info
                        WHERE 工作中心名称 IS NOT NULL
                          AND 工作中心名称 <> '';""")
        result = await session.execute(query)
        return [row[0] for row in result]

@register_values_getter("工序名称")
@register_dimension("工序名称")
async def query_allowed_product_name() -> list[str]:
    async with get_readonly_session('mes') as session:
        query = text("""SELECT DISTINCT 工序名称
                        FROM pv_uex_daq_info
                        WHERE 工序名称 IS NOT NULL
                          AND 工序名称 <> ''
                           <> 0;""")
        result = await session.execute(query)
        return [row[0] for row in result]


@register_values_getter("物料名称")
@register_dimension("物料名称")
async def query_allowed_product_name() -> list[str]:
    async with get_readonly_session('mes') as session:
        query = text("""SELECT DISTINCT MRL_NAME
                        FROM pv_uqcm_chk_bill_cl
                        WHERE MRL_NAME IS NOT NULL
                          AND MRL_NAME <> ''
                           <> 0;""")
        result = await session.execute(query)
        return [row[0] for row in result]

@register_values_getter("任务编号")
@register_dimension("任务编号")
async def query_allowed_product_name() -> list[str]:
    async with get_readonly_session('mes') as session:
        query = text("""SELECT DISTINCT TASK_CODE
                        FROM wms_in_task
                        WHERE TASK_CODE IS NOT NULL
                          AND IS_DELETE <> 1
                           <> 0;""")
        result = await session.execute(query)
        return [row[0] for row in result]
