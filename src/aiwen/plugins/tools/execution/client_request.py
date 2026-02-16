"""Client request tool."""

import logging

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

logger = logging.getLogger(__name__)


class ClientRequestInnerTool(InnerTool):
    """Execute a request on the user's client (browser)

    This is the built-in execution backend for user-defined CLIENT_RUN tools.
    ExternalTool instances delegate to this tool to send instructions to the
    user's browser, where a registered frontend handler picks up and executes
    the request.
    """

    METADATA = ToolMetadata(
        name="client_request",
        display_name="Client Request",
        description="Send a request to be executed on the user's client (browser-side handler)",
        category="execution",
        tags=["client", "browser", "request", "inner"],
        timeout=120,
    )

    class InputSchema(ToolInputSchema):
        handler_name: str = Field(
            description="Name of the frontend handler to invoke (e.g., 'filePicker', 'clipboard', 'notification')",
        )
        action: str = Field(
            description="Action for the handler to perform (e.g., 'read', 'write', 'show')",
        )
        params: dict = Field(
            default_factory=dict,
            description="Parameters to pass to the client handler",
        )
        require_approval: bool = Field(
            default=True,
            description="Whether user approval is required before execution",
        )
        timeout_ms: int = Field(
            default=60000,
            ge=1000,
            le=300000,
            description="Client-side execution timeout in milliseconds",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Package the client request payload for delivery to the frontend."""
        if not input_data.handler_name:
            return ToolOutputSchema(
                success=False,
                error="No handler_name provided for client request",
            )

        payload = {
            "type": "client_request",
            "handler_name": input_data.handler_name,
            "action": input_data.action,
            "params": input_data.params,
            "require_approval": input_data.require_approval,
            "timeout_ms": input_data.timeout_ms,
        }

        logger.info(
            f"Client request prepared: handler={input_data.handler_name}, "
            f"action={input_data.action}"
        )

        return ToolOutputSchema(
            success=True,
            message=f"Client request dispatched to handler '{input_data.handler_name}'",
            data=payload,
        )
