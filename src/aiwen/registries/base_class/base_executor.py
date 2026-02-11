"""
Base Executor Class

Re-exports the Executor protocol from core.interfaces for backward compatibility.
All executors should implement the ExecutorProtocol interface.
"""

# Import the actual Executor implementation from core.interfaces
from aiwen.core.interfaces.executor import (
    AgentEvent,
    Executor,
    WaitingForTool,
)

__all__ = [
    "Executor",
    "AgentEvent",
    "WaitingForTool",
]
