"""Confirm-action tool — example of a Human-in-the-Loop (HITL) tool.

This tool demonstrates the full user-approval flow. The tool itself is
ordinary; the approval gate is activated by adding ``"confirm_action"``
to the ``approval_tools`` list in the executor configuration.

HITL flow
---------
1. The LLM decides to call ``confirm_action``.
2. The executor detects that ``"confirm_action"`` is in ``approval_tools``.
3. The executor emits a ``TOOL_PENDING`` event and raises ``WaitingForTool``.
4. The run transitions to the **waiting** state.
5. The client displays the pending action (tool name + arguments) to the user.
6. The user approves or rejects via::

       POST /workspaces/{workspace_id}/runs/{run_id}/resume
       { "approved": true,  "comment": "Looks good" }
       { "approved": false, "comment": "Too risky"  }

7. If **approved**: this tool executes and returns the confirmation result.
8. If **rejected**: the executor injects a rejection error into the
   conversation and the LLM continues without executing the action.

Configuration example
---------------------
To activate HITL for this tool, include it in the executor config:

.. code-block:: python

    executor_config = {
        "approval_tools": ["confirm_action"],
        ...
    }

Or via the workspace/app config JSON:

.. code-block:: json

    {
      "approval_tools": ["confirm_action"]
    }
"""

from datetime import UTC, datetime
import logging

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

logger = logging.getLogger(__name__)


class ConfirmActionInnerTool(InnerTool):
    """Perform a user-confirmed action.

    When ``"confirm_action"`` is listed in the executor's ``approval_tools``,
    the run pauses and the user must explicitly approve or reject before this
    tool executes.  Use this pattern for any operation that is irreversible,
    has side-effects, or requires explicit human oversight.

    Input fields are intentionally descriptive so the client can present a
    clear approval dialog to the user.
    """

    METADATA = ToolMetadata(
        name="confirm_action",
        display_name="Confirm Action",
        description=(
            "Request explicit user confirmation before performing a sensitive "
            "or irreversible action. The run pauses until the user approves or "
            "rejects the request. Add 'confirm_action' to the executor's "
            "'approval_tools' list to activate the HITL approval gate."
        ),
        category="execution",
        tags=["approval", "hitl", "confirmation", "inner"],
        timeout=300,  # 5 min — user may take time to review
    )

    class InputSchema(ToolInputSchema):
        action_name: str = Field(
            description="Short name of the action to be performed (e.g. 'delete_user_data')",
        )
        description: str = Field(
            description="Human-readable description of what the action will do and why",
        )
        risk_level: str = Field(
            default="medium",
            description="Risk level of the action: 'low', 'medium', or 'high'",
            pattern="^(low|medium|high)$",
        )
        target: str | None = Field(
            default=None,
            description="The resource or entity the action will affect (e.g. a file path, user ID)",
        )
        metadata: dict | None = Field(
            default=None,
            description="Optional additional context for the approval dialog",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute the confirmed action.

        This method is only called *after* the user has approved the action
        (when HITL is enabled).  It records the approval timestamp and
        returns a structured confirmation that the LLM can reference in its
        response.
        """
        approved_at = datetime.now(UTC).isoformat()

        logger.info(
            "confirm_action approved and executed: action=%s risk=%s target=%s",
            input_data.action_name,
            input_data.risk_level,
            input_data.target,
        )

        return ToolOutputSchema(
            success=True,
            message=(
                f"Action '{input_data.action_name}' confirmed and executed successfully."
            ),
            data={
                "action_name": input_data.action_name,
                "description": input_data.description,
                "risk_level": input_data.risk_level,
                "target": input_data.target,
                "metadata": input_data.metadata,
                "approved_at": approved_at,
                "status": "executed",
            },
        )
