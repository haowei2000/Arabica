"""Code execution tool."""

import logging
from typing import Any

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

logger = logging.getLogger(__name__)


class CodeExecutionInnerTool(InnerTool):
    """Execute Python code with input data in a sandboxed context

    This is the built-in execution backend for user-defined SERVER_RUN tools.
    ExternalTool instances delegate to this tool to run user-provided Python code.
    """

    METADATA = ToolMetadata(
        name="code_execution",
        display_name="Code Execution",
        description="Execute Python code with input data in a controlled context",
        category="execution",
        tags=["code", "python", "execution", "inner"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        code: str = Field(description="Python code to execute")
        input_data: dict = Field(
            default_factory=dict,
            description="Input parameters available to the code as 'input_data' variable",
        )

    async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        """Execute Python code with input_data in execution context"""
        typed_input = self.InputSchema.model_validate(input_data.model_dump())

        if not typed_input.code:
            return ToolOutputSchema(
                success=False,
                error="No code provided for execution",
            )

        try:
            context: dict[str, Any] = {
                "input_data": typed_input.input_data,
                "__builtins__": __builtins__,
            }

            exec(typed_input.code, context)

            if "result" in context:
                result = context["result"]
                return ToolOutputSchema(
                    success=True,
                    message="Code executed successfully",
                    data=result if isinstance(result, dict) else {"result": result},
                )
            return ToolOutputSchema(
                success=False,
                error="Code did not produce a 'result' variable",
            )

        except Exception as e:
            logger.error(f"Code execution error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"Execution error: {e!s}",
            )
