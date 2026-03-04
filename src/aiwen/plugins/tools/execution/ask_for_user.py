"""ask_for_user tool — pause execution to collect information from the user.

When the agent needs clarification or additional input that cannot be
inferred from available context, it calls this tool.  The tool raises
``WaitingForUserInput`` which causes the framework to:

1. Transition the run to the ``waiting`` state.
2. Publish an ``agent.query`` event carrying the question text.
3. Wait for the user to respond via ``POST /runs/{id}/feedback``.
4. Resume the agentic loop with the user's answer injected as the
   tool result for this call.

Usage example
-------------
The LLM emits:

    <tool_call>
    {"name": "ask_for_user", "arguments": {"question": "Which database should I use?"}}
    </tool_call>

The frontend displays the question; the user replies; the agent
continues with full context of the answer.
"""

import logging

from pydantic import Field

from aiwen.core.interfaces.executor import WaitingForUserInput
from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

logger = logging.getLogger(__name__)

# TODO : fix ask for user tool
# class AskForUserTool(InnerTool):
#     """Ask the user a question and wait for their response.
#
#     Use this tool when you need information from the user before you can
#     continue.  The run pauses until the user answers; their response is
#     then available as the tool result so you can proceed.
#
#     Keep questions concise and specific.  Prefer a single question per
#     call; if you have multiple questions, prioritize the most important one.
#     """
#
#     METADATA = ToolMetadata(
#         name="ask_for_user",
#         display_name="Ask User",
#         description=(
#             "Pause execution and ask the user a question. "
#             "Use when you need clarification or additional information "
#             "from the user before you can complete the task. "
#             "The run waits until the user responds."
#         ),
#         category="execution",
#         tags=["user", "input", "clarification", "interactive"],
#         timeout=1800,  # 30 min — user may take time to respond
#     )
#
#     class InputSchema(ToolInputSchema):
#         question: str = Field(
#             description="The question to ask the user. Be specific and concise.",
#         )
#         context: str | None = Field(
#             default=None,
#             description=(
#                 "Optional context explaining why you need this information "
#                 "(shown to the user alongside the question)."
#             ),
#         )
#
#     async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
#         """Raise WaitingForUserInput to pause the run and display the question."""
#         logger.info("ask_for_user: pausing run to collect user input")
#         # TODO ADD logic to finish the tool
#         raise WaitingForUserInput(question=input_data.question)
