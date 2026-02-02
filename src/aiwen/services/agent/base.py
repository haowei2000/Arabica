# aiwen/services/agent/base.py
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, ClassVar


class Executor(ABC):
    """Base class for all agent in the system."""

    # Template metadata - must be defined by subclasses
    TEMPLATE: ClassVar[dict[str, Any]]

    def __init__(self, config: dict):
        """
        Initialize the agent with configuration.

        Args:
            config: Configuration dictionary for the agent
        """
        self.config = config

    @abstractmethod
    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the agent with the given input data.

        Args:
            input_data: Input data for the agent to process

        Returns:
            Dictionary containing the agent's output
        """
        pass

    async def stream(self, input_data: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Stream the agent's output as it is generated.

        Args:
            input_data: Input data for the agent to process

        Yields:
            Chunks of the agent's output as strings
        """
        # Default implementation - subclasses should override for true streaming
        result = await self.run(input_data)
        # Convert result to string and yield it
        yield str(result)
