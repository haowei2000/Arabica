"""
Registry Manager - Central coordinator for all registries.

Provides:
- Singleton access to all registries
- Unified initialization
- Batch database synchronization
- Cross-registry operations
"""

from __future__ import annotations

import logging
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from structure.registries.core import BaseRegistry

T = TypeVar("T", bound=BaseRegistry)

logger = logging.getLogger(__name__)


class RegistryManager:
    """
    Centralized manager for all registries.

    Singleton pattern ensures only one manager instance exists.
    Coordinates lifecycle operations across all registered registries.

    Usage:
        manager = RegistryManager.get_instance()
        tool_registry = manager.get_registry(ToolRegistry)
        await manager.sync_all_to_database(db)
    """

    _instance: RegistryManager | None = None
    _registries: dict[type[BaseRegistry], BaseRegistry] = {}

    def __new__(cls):
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> RegistryManager:
        """
        Get a singleton instance of RegistryManager.

        Returns:
            RegistryManager instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_registry(
        self, registry_cls: type[T], instance: T | None = None
    ) -> T:
        """
        Register a registry type with the manager.

        Args:
            registry_cls: Registry class type
            instance: Optional pre-created instance (creates new if None)

        Returns:
            Registry instance
        """
        if registry_cls in self._registries:
            logger.warning(f"Registry {registry_cls.__name__} already registered")
            return self._registries[registry_cls]  # type: ignore[return-value]

        if instance is None:
            instance = registry_cls()

        self._registries[registry_cls] = instance
        logger.info(f"Registered registry: {registry_cls.__name__}")
        return instance

    def get_registry(self, registry_cls: type[T]) -> T:
        """
        Get registry instance by type.

        Args:
            registry_cls: Registry class type

        Returns:
            Registry instance

        Raises:
            KeyError: If registry not registered
        """
        if registry_cls not in self._registries:
            raise KeyError(
                f"Registry {registry_cls.__name__} not registered. "
                f"Available: {list(self._registries.keys())}"
            )
        return self._registries[registry_cls]  # ty:ignore[invalid-return-type]

    def get_all_registries(self) -> list[BaseRegistry]:
        """
        Get all registered registries.

        Returns:
            List of registry instances
        """
        return list(self._registries.values())

    def is_registered(self, registry_cls: type[BaseRegistry]) -> bool:
        """
        Check if registry type is registered.

        Args:
            registry_cls: Registry class type

        Returns:
            True if registered
        """
        return registry_cls in self._registries

    async def sync_all_to_database(self, db: AsyncSession) -> None:
        """
        Sync all registries to database.

        Args:
            db: Database session
        """
        logger.info("Syncing all registries to database...")

        for registry_cls, registry in self._registries.items():
            try:
                await registry.sync_to_database(db)
                logger.info(f"✓ Synced {registry_cls.__name__}")
            except Exception as e:
                logger.error(f"✗ Failed to sync {registry_cls.__name__}: {e}")

        logger.info("All registries synced")

    def get_statistics(self) -> dict[str, dict[str, any]]:
        """
        Get statistics from all registries.

        Returns:
            Dictionary mapping registry name to statistics
        """
        return {
            registry_cls.__name__: registry.get_statistics()
            for registry_cls, registry in self._registries.items()
        }

    def clear_all(self) -> None:
        """
        Clear all registries (useful for testing).
        """
        logger.warning("Clearing all registries...")
        for registry in self._registries.values():
            registry.clear()
        logger.info("All registries cleared")

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"RegistryManager(registries={len(self._registries)}, "
            f"types={[cls.__name__ for cls in self._registries.keys()]})"
        )


# ==================== Convenience Functions ====================


def get_registry(registry_cls: type[T]) -> T:
    """
    Convenience function to get registry instance.

    Args:
        registry_cls: Registry class type

    Returns:
        Registry instance

    Usage:
        from structure.registries import get_registry, ToolRegistry
        tool_registry = get_registry(ToolRegistry)
    """
    manager = RegistryManager.get_instance()
    return manager.get_registry(registry_cls)


def register_registry(registry_cls: type[T], instance: T | None = None) -> T:
    """
    Convenience function to register a registry.

    Args:
        registry_cls: Registry class type
        instance: Optional pre-created instance

    Returns:
        Registry instance
    """
    manager = RegistryManager.get_instance()
    return manager.register_registry(registry_cls, instance)


async def sync_all_registries(db: AsyncSession) -> None:
    """
    Convenience function to sync all registries to database.

    Args:
        db: Database session
    """
    manager = RegistryManager.get_instance()
    await manager.sync_all_to_database(db)
