# aiwen/workers/executor_worker.py
import asyncio
import logging
from uuid import UUID

import redis.asyncio as redis_async
from aiwen.services.workspace.workspace import WorkspaceManager
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.services.agent.agent_registry import AgentRegistry
from aiwen.services.agent.base import BaseAgentTemplate
from aiwen.services.agent.runtime import AgentRuntime
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus

logger = logging.getLogger(__name__)


class Worker:
    """
    Worker: 消费 Redis Stream 的 Run 任务，调度对应 Executor。
    """

    def __init__(self, redis_client: redis_async.Redis, db: AsyncSession):
        self.redis = redis_client
        self.db = db
        self.runtime = AgentRuntime()  # 管理活跃 Executor 实例
        self.workspace = WorkspaceManager()  # 管理 workspace 上下文
        self.state_machine = RunStateMachine(db, redis_client)

    async def start(self, stream_name: str):
        logger.info(f"Worker listening on stream: {stream_name}")
        while True:
            try:
                messages = await self.redis.xread({stream_name: "$"}, count=1, block=1000)
                if not messages:
                    continue
                _, msg_list = messages[0]
                for message_id, data in msg_list:
                    asyncio.create_task(self.handle_message(data))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def handle_message(self, message_data: dict):
        """
        消息结构: {"run_id": str, "executor_code": str, "input": dict, "triggered_by": str}
        """
        try:
            run_id = UUID(message_data["run_id"])
            executor_code = message_data["executor_code"]
            input_data = message_data.get("input", {})
            triggered_by = message_data.get("triggered_by", "user")

            executor_cls = AgentRegistry.get(executor_code)
            if not executor_cls:
                raise ValueError(f"Executor '{executor_code}' not registered")

            # 创建 agent 实例
            executor: BaseAgentTemplate = executor_cls(run_id, self.db, self.redis, self.workspace)

            # 附加到 runtime
            self.runtime.attach(run_id, executor)

            # 根据 Run 状态决定行为
            async with self.db.begin():
                stmt_run = await self.state_machine.db.execute(
                    f"SELECT * FROM run WHERE id='{run_id}'"
                )
                run = stmt_run.scalar_one_or_none()
                if not run:
                    raise ValueError(f"Run {run_id} not found")

                if RunStateMachine.is_terminal(run.status):
                    logger.warning(f"Run {run_id} is terminal, skipping execution")
                    return

                # 启动或恢复
                if run.status == RunStatus.PENDING.value:
                    await self.state_machine.start(run_id, triggered_by=triggered_by)
                    await self._execute_run(executor, input_data, run_id)
                elif run.status == RunStatus.WAITING.value:
                    await self.state_machine.resume_from_tool(run_id)
                    await self._execute_run(executor, input_data, run_id)
                elif run.status == RunStatus.RUNNING.value:
                    # 恢复中断 run
                    await self._execute_run(executor, input_data, run_id)

        except Exception as e:
            logger.error(f"handle_message error: {e}", exc_info=True)
            try:
                run_id = UUID(message_data.get("run_id"))
                await self.state_machine.fail(run_id, error=str(e))
            except Exception:
                pass

    async def _execute_run(self, executor: BaseAgentTemplate, input_data: dict, run_id: UUID):
        """
        核心执行流程，支持 token 流式输出、工具等待、取消。
        """
        try:
            # 流式输出
            async for token in executor.stream(input_data):
                # token 发布事件
                await self._publish_token(run_id, token)

                # 增量 push workspace 上下文
                await self.workspace.push(run_id, token)

                # 检查 run 状态是否被 cancel
                run = await self.state_machine.db.get(executor.run_model_cls, run_id)
                if run.status == RunStatus.CANCELLED.value:
                    logger.info(f"Run {run_id} cancelled, stopping agent")
                    await executor.cancel()
                    return

            # 执行完成
            await self.state_machine.complete(run_id, output_data=executor.get_final_output())
            logger.info(f"Run {run_id} completed")

        except executor.WaitingForTool as e:
            # 工具等待
            await self.state_machine.pause_for_tool(run_id, waiting_for=e.info)
            logger.info(f"Run {run_id} paused for tool {e.info}")
        except Exception as e:
            logger.error(f"Executor failed for run {run_id}: {e}", exc_info=True)
            await self.state_machine.fail(run_id, error=str(e))
        finally:
            self.runtime.release(run_id)

    async def _publish_token(self, run_id: UUID, token: dict):
        """
        token 格式: {"type": "text|tool|chunk", "data": "..."}
        发布 token 到 Redis Stream
        """
        stream_name = f"run:{run_id}:tokens"
        try:
            await self.redis.xadd(
                stream_name,
                fields={
                    "type": token.get("type", "chunk"),
                    "data": str(token.get("data", "")),
                },
                maxlen=1000,
                approximate=True,
            )
        except Exception as e:
            logger.error(f"Error publishing token for run {run_id}: {e}")
