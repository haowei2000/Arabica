"""Sandbox execution tool."""

import contextlib
import logging
from typing import Any

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema

logger = logging.getLogger(__name__)


class SandboxExecutionInnerTool(InnerTool):
    """Execute a command in an isolated sandbox (Docker container)

    This is the built-in execution backend for user-defined CONTAINER_RUN tools.
    ExternalTool instances delegate to this tool to run arbitrary shell commands
    inside a short-lived Docker container with configurable resource limits.
    """

    METADATA = ToolMetadata(
        name="sandbox_execution",
        display_name="Sandbox Execution",
        description="Execute a shell command in an isolated Docker sandbox with resource limits",
        category="execution",
        tags=["sandbox", "container", "docker", "shell", "inner"],
        timeout=120,
    )

    class InputSchema(ToolInputSchema):
        command: str = Field(
            description="Shell command to execute inside the sandbox",
        )
        image: str = Field(
            default="python:3.12-slim",
            description="Docker image to use for the sandbox",
        )
        timeout_seconds: int = Field(
            default=60,
            ge=1,
            le=600,
            description="Execution timeout in seconds",
        )
        memory_limit: str = Field(
            default="256m",
            description="Memory limit (e.g., '256m', '1g')",
        )
        network_enabled: bool = Field(
            default=False,
            description="Whether to allow network access inside the sandbox",
        )
        workdir: str = Field(
            default="/workspace",
            description="Working directory inside the container",
        )
        environment: dict[str, str] = Field(
            default_factory=dict,
            description="Additional environment variables for the container",
        )
        stdin_data: str | None = Field(
            default=None,
            description="Optional data to pass via stdin",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute a command inside a Docker container sandbox."""
        import asyncio

        if not input_data.command:
            return ToolOutputSchema(
                success=False,
                error="No command provided for sandbox execution",
            )

        logger.info(
            f"Sandbox execution: image={input_data.image}, "
            f"command={input_data.command[:100]}..."
        )

        try:
            import docker
            from docker.errors import ContainerError, ImageNotFound

            client = docker.from_env()

            container_kwargs: dict[str, Any] = {
                "image": input_data.image,
                "command": ["sh", "-c", input_data.command],
                "working_dir": input_data.workdir,
                "mem_limit": input_data.memory_limit,
                "pids_limit": 100,
                "network_disabled": not input_data.network_enabled,
                "read_only": True,
                "detach": True,
                "stdout": True,
                "stderr": True,
                "environment": {
                    "PYTHONUNBUFFERED": "1",
                    **input_data.environment,
                },
                "tmpfs": {"/tmp": "size=64m"},
            }

            if input_data.stdin_data:
                container_kwargs["stdin_open"] = True

            container = client.containers.run(**container_kwargs)

            try:
                result = await asyncio.to_thread(
                    container.wait, timeout=input_data.timeout_seconds
                )
                exit_code = result.get("StatusCode", -1)

                stdout = (
                    await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                ).decode("utf-8", errors="replace")
                stderr = (
                    await asyncio.to_thread(container.logs, stdout=False, stderr=True)
                ).decode("utf-8", errors="replace")
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(container.remove, force=True)

            return ToolOutputSchema(
                success=exit_code == 0,
                message=f"Command exited with code {exit_code}",
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                    "image": input_data.image,
                    "command": input_data.command,
                },
            )

        except ImageNotFound:
            return ToolOutputSchema(
                success=False,
                error=f"Docker image not found: {input_data.image}",
            )
        except ContainerError as e:
            return ToolOutputSchema(
                success=False,
                error=f"Container error: {e!s}",
                data={
                    "exit_code": e.exit_status,
                    "stderr": e.stderr.decode("utf-8", errors="replace")
                    if e.stderr
                    else "",
                },
            )
        except Exception as e:
            logger.error(f"Sandbox execution error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"Sandbox execution failed: {e!s}",
            )
