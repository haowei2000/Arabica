"""
Example: Creating a New Registry Type

This example shows how to extend the registry system with a ModelRegistry
for managing LLM model configurations.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from aiwen.registries.base import BaseRegistry, RegistryConfig
from sqlalchemy.ext.asyncio import AsyncSession

# ==================== 1. Define Component Type ====================


@dataclass
class ModelConfig:
    """Configuration for an LLM model."""

    model_name: str  # Unique identifier (e.g., "gpt-4", "claude-3")
    display_name: str  # Human-readable name
    provider: str  # Provider (e.g., "openai", "anthropic", "ollama")
    api_base: str | None = None  # API base URL
    max_tokens: int = 4096  # Maximum context length
    temperature: float = 0.7  # Default temperature
    enabled: bool = True  # Whether model is enabled
    metadata: dict[str, Any] | None = None  # Additional metadata


# ==================== 2. Create Registry Class ====================


class ModelRegistry(BaseRegistry[str, ModelConfig]):
    """
    Registry for LLM model configurations.

    Manages model configurations with filtering by provider, validation,
    and optional database persistence.
    """

    def __init__(self, config: RegistryConfig | None = None):
        """Initialize model registry with default config."""
        if config is None:
            config = RegistryConfig(
                enable_db_sync=False,  # Can be enabled if using DB
                enable_instance_cache=False,  # Configs are lightweight
                validate_on_register=True,
                allow_override=True,  # Allow updating model configs
                log_registration=True,
            )
        super().__init__(config)

    # ==================== Implement Abstract Methods ====================

    def _validate_component(self, model_config: ModelConfig) -> None:
        """
        Validate model configuration.

        Args:
            model_config: Model configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        if not model_config.model_name:
            raise ValueError("model_name is required")

        if not model_config.display_name:
            raise ValueError("display_name is required")

        if not model_config.provider:
            raise ValueError("provider is required")

        if model_config.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        if not (0.0 <= model_config.temperature <= 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")

    def _extract_key(self, model_config: ModelConfig) -> str:
        """
        Extract model name as unique key.

        Args:
            model_config: Model configuration

        Returns:
            Model name (unique identifier)
        """
        return model_config.model_name

    def _create_instance(
        self, key: str, model_config: ModelConfig, **kwargs
    ) -> ModelConfig:
        """
        Return model config (no instantiation needed).

        For more complex scenarios, this could create an actual
        LLM client instance (e.g., OpenAI, Anthropic client).

        Args:
            key: Model name
            model_config: Model configuration
            **kwargs: Additional arguments

        Returns:
            Model configuration
        """
        # Simple case: return config as-is
        return model_config

        # Complex case: create actual client
        # from openai import OpenAI
        # return OpenAI(api_key=kwargs.get("api_key"), base_url=model_config.api_base)

    async def _sync_to_database(self, db: AsyncSession) -> None:
        """
        Sync model configurations to database (optional).

        Args:
            db: Database session
        """
        # If you have a ModelConfig database table, sync here
        # For this example, we skip database persistence
        pass

    # ==================== Model-Specific Methods ====================

    def list_by_provider(self, provider: str, enabled_only: bool = True) -> list[str]:
        """
        List models by provider.

        Args:
            provider: Provider name (e.g., "openai", "anthropic")
            enabled_only: Return only enabled models

        Returns:
            List of model names
        """

        def predicate(name: str, config: ModelConfig) -> bool:
            if config.provider != provider:
                return False
            if enabled_only and not config.enabled:
                return False
            return True

        filtered = self.filter(predicate)
        return sorted(filtered.keys())

    def get_model_info(self, model_name: str) -> dict[str, Any] | None:
        """
        Get detailed model information.

        Args:
            model_name: Model name

        Returns:
            Model information dictionary or None
        """
        config = self.get(model_name)
        if not config:
            return None

        return {
            "model_name": config.model_name,
            "display_name": config.display_name,
            "provider": config.provider,
            "api_base": config.api_base,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "enabled": config.enabled,
            "metadata": config.metadata,
        }


# ==================== 3. Create Decorator ====================


def register_model(model_config: ModelConfig) -> ModelConfig:
    """
    Decorator to register model configuration.

    Example:
        ```python
        @register_model
        def gpt4_config():
            return ModelConfig(
                model_name="gpt-4",
                display_name="GPT-4",
                provider="openai",
                max_tokens=8192
            )
        ```

    Args:
        model_config: Model configuration

    Returns:
        Registered model configuration
    """
    from aiwen.registries.manager import RegistryManager

    manager = RegistryManager.get_instance()

    # Register ModelRegistry if not already registered
    if not manager.is_registered(ModelRegistry):
        manager.register_registry(ModelRegistry)

    registry = manager.get_registry(ModelRegistry)
    return registry.register(model_config)


# ==================== 4. Example Usage ====================


def example_usage():
    """Example usage of ModelRegistry."""
    from aiwen.registries.manager import RegistryManager, get_registry

    # Register ModelRegistry with manager
    manager = RegistryManager.get_instance()
    manager.register_registry(ModelRegistry)

    # Get registry instance
    registry = get_registry(ModelRegistry)

    # Register some models
    gpt4 = ModelConfig(
        model_name="gpt-4",
        display_name="GPT-4",
        provider="openai",
        max_tokens=8192,
        temperature=0.7,
    )
    registry.register(gpt4)

    claude3 = ModelConfig(
        model_name="claude-3-opus",
        display_name="Claude 3 Opus",
        provider="anthropic",
        max_tokens=200000,
        temperature=0.7,
    )
    registry.register(claude3)

    ollama_llama = ModelConfig(
        model_name="llama3",
        display_name="Llama 3",
        provider="ollama",
        api_base="http://localhost:11434",
        max_tokens=4096,
        temperature=0.8,
    )
    registry.register(ollama_llama)

    # List all models
    print("All models:", registry.list_keys())
    # Output: ['claude-3-opus', 'gpt-4', 'llama3']

    # Filter by provider
    openai_models = registry.list_by_provider("openai")
    print("OpenAI models:", openai_models)
    # Output: ['gpt-4']

    # Get model config
    config = registry.get("gpt-4")
    print(f"GPT-4 max tokens: {config.max_tokens}")
    # Output: GPT-4 max tokens: 8192

    # Get detailed info
    info = registry.get_model_info("claude-3-opus")
    print("Claude 3 info:", info)
    # Output: {'model_name': 'claude-3-opus', 'display_name': 'Claude 3 Opus', ...}

    # Statistics
    stats = registry.get_statistics()
    print("Registry stats:", stats)
    # Output: {'total_components': 3, 'cached_instances': 0, ...}


if __name__ == "__main__":
    example_usage()
