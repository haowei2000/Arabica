"""
Sandbox executor for running tools in isolated Docker containers.

This executor provides secure, isolated execution of untrusted code
with configurable resource limits.
"""

import asyncio
import json
import logging
import time
from typing import Any

from aiwen.schemas.tools.execution import ExecutionContext, ToolResult
from aiwen.services.executor.tools.execution_mode import (
    ToolExecutionMode,
    ToolMetadata,
)

logger = logging.getLogger(__name__)

# Template for the wrapper script that runs inside the container
SANDBOX_WRAPPER_SCRIPT = """
import json
import sys

def main():
    # Read arguments from stdin
    args_json = sys.stdin.read()
    args = json.loads(args_json)

    tool_name = args.get("tool_name")
    arguments = args.get("arguments", {})

    try:
        # Import and execute the tool
        # Tools are expected to be pure functions
        from tool_module import execute
        result = execute(**arguments)

        output = {
            "success": True,
            "result": result
        }
    except Exception as e:
        output = {
            "success": False,
            "error": str(e)
        }

    print(json.dumps(output))

if __name__ == "__main__":
    main()
"""


class SandboxExecutor:
    """
    Execute tools in isolated Docker containers.

    Features:
    - Resource limits (memory, CPU, PIDs)
    - Network isolation
    - Timeout enforcement
    - Automatic cleanup
    """

    def __init__(self, docker_url: str | None = None):
        """
        Initialize the sandbox executor.

        Args:
            docker_url: Docker daemon URL (defaults to local socket)
        """
        self._docker_url = docker_url
        self._docker: Any = None  # aiodocker.Docker instance

    async def _get_docker(self) -> Any:
        """Get or create Docker client."""
        if self._docker is None:
            try:
                import aiodocker

                self._docker = aiodocker.Docker(url=self._docker_url)
            except ImportError:
                raise RuntimeError(
                    "aiodocker is required for sandbox execution. "
                    "Install with: uv add aiodocker"
                )
        return self._docker

    async def execute(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        metadata: ToolMetadata,
        context: ExecutionContext,
    ) -> ToolResult:
        """
        Execute a tool in a Docker container.

        Args:
            tool_name: Name of the tool
            tool_id: Unique invocation ID
            arguments: Tool arguments
            metadata: Tool execution metadata
            context: Execution context

        Returns:
            ToolResult with execution outcome
        """
        start_time = time.perf_counter()
        container = None

        try:
            docker = await self._get_docker()

            # Prepare container configuration
            config = self._build_container_config(
                tool_name=tool_name,
                arguments=arguments,
                metadata=metadata,
            )

            logger.info(
                f"Creating sandbox container for tool '{tool_name}' "
                f"(image={metadata.sandbox_image}, timeout={metadata.timeout_seconds}s)"
            )

            # Create and start container
            container = await docker.containers.create(config=config)
            await container.start()

            # Wait for completion with timeout
            try:
                exit_info = await asyncio.wait_for(
                    container.wait(),
                    timeout=metadata.timeout_seconds,
                )
                exit_code = exit_info.get("StatusCode", -1)
            except TimeoutError:
                logger.warning(
                    f"Sandbox execution timed out after {metadata.timeout_seconds}s "
                    f"for tool '{tool_name}'"
                )
                await self._kill_container(container)
                execution_time_ms = int((time.perf_counter() - start_time) * 1000)

                return ToolResult(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    success=False,
                    error_message=f"Execution timed out after {metadata.timeout_seconds} seconds",
                    execution_time_ms=execution_time_ms,
                    execution_mode=ToolExecutionMode.SANDBOX.value,
                    sandbox_container_id=container.id if container else None,
                )

            # Collect output
            stdout = await container.log(stdout=True)
            stderr = await container.log(stderr=True)

            stdout_text = "".join(stdout) if stdout else ""
            stderr_text = "".join(stderr) if stderr else ""

            execution_time_ms = int((time.perf_counter() - start_time) * 1000)

            # Parse result from stdout
            result, error = self._parse_output(stdout_text, exit_code)

            if error:
                return ToolResult(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    success=False,
                    error_message=error,
                    execution_time_ms=execution_time_ms,
                    execution_mode=ToolExecutionMode.SANDBOX.value,
                    sandbox_container_id=container.id,
                    stdout=stdout_text,
                    stderr=stderr_text,
                )

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=True,
                result=result,
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.SANDBOX.value,
                sandbox_container_id=container.id,
                stdout=stdout_text,
                stderr=stderr_text,
            )

        except Exception as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            logger.exception(f"Sandbox execution error for tool '{tool_name}': {e}")

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=False,
                error_message=f"Sandbox error: {e}",
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.SANDBOX.value,
                sandbox_container_id=container.id if container else None,
            )

        finally:
            # Cleanup container
            if container:
                await self._cleanup_container(container)

    def _build_container_config(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        metadata: ToolMetadata,
    ) -> dict[str, Any]:
        """Build Docker container configuration."""
        limits = metadata.resource_limits

        # Prepare input data
        input_data = json.dumps(
            {
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )

        config: dict[str, Any] = {
            "Image": metadata.sandbox_image or "python:3.12-slim",
            "Cmd": [
                "python",
                "-c",
                f"import sys; exec({SANDBOX_WRAPPER_SCRIPT!r})",
            ],
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "OpenStdin": True,
            "StdinOnce": True,
            "WorkingDir": metadata.sandbox_workdir,
            "HostConfig": {
                "Memory": self._parse_memory_limit(limits.memory),
                "MemorySwap": self._parse_memory_limit(limits.memory),  # No swap
                "CpuPeriod": limits.cpu_period,
                "CpuQuota": limits.cpu_quota,
                "PidsLimit": limits.pids_limit,
                "NetworkMode": "none" if limits.network_disabled else "bridge",
                "ReadonlyRootfs": limits.read_only_rootfs,
                "AutoRemove": False,  # We handle cleanup
            },
            "NetworkDisabled": limits.network_disabled,
        }

        return config

    def _parse_memory_limit(self, memory: str) -> int:
        """Parse memory limit string (e.g., '256m') to bytes."""
        memory = memory.lower().strip()

        if memory.endswith("g"):
            return int(float(memory[:-1]) * 1024 * 1024 * 1024)
        if memory.endswith("m"):
            return int(float(memory[:-1]) * 1024 * 1024)
        if memory.endswith("k"):
            return int(float(memory[:-1]) * 1024)
        return int(memory)

    def _parse_output(self, stdout: str, exit_code: int) -> tuple[Any, str | None]:
        """Parse container output to extract result or error."""
        if exit_code != 0:
            return None, f"Container exited with code {exit_code}"

        stdout = stdout.strip()
        if not stdout:
            return None, "No output from container"

        try:
            # Find last JSON object in output (in case of debug prints)
            lines = stdout.split("\n")
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{"):
                    output = json.loads(line)
                    if output.get("success"):
                        return output.get("result"), None
                    return None, output.get("error", "Unknown error")

            return None, "No valid JSON output found"

        except json.JSONDecodeError as e:
            return None, f"Failed to parse output: {e}"

    async def _kill_container(self, container: Any) -> None:
        """Kill a running container."""
        try:
            await container.kill()
        except Exception as e:
            logger.warning(f"Failed to kill container: {e}")

    async def _cleanup_container(self, container: Any) -> None:
        """Remove a container."""
        try:
            await container.delete(force=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup container: {e}")

    async def close(self) -> None:
        """Close the Docker client."""
        if self._docker:
            await self._docker.close()
            self._docker = None
