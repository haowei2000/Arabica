"""
Unified Registry System - Core Implementation

This module combines the base registry abstraction with concrete implementations
for a more cohesive and maintainable structure.

Architecture:
    BaseRegistry (Abstract) → Provides template method pattern
        ├── ToolRegistry (Concrete) → Manages tool classes
        └── ExecutorRegistry (Concrete) → Manages executor templates
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.enums import ToolExecutionMode
from aiwen.registries.base_class.base_tool import BaseTool
from aiwen.registries.base_class.base_executor import Executor

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class RegistryConfig:
    """
    Configuration for registry behavior.

    Controls how registries handle caching, validation, persistence, etc.
    """

    # Persistence options
    enable_db_sync: bool = False  # Sync to database on startup
    enable_lazy_load: bool = False  # Load components on-demand

    # Caching options
    enable_instance_cache: bool = True  # Cache instances (singleton pattern)
    cache_ttl: int | None = None  # Cache TTL in seconds (None = forever)

    # Validation options
    validate_on_register: bool = True  # Validate component on registration
    allow_override: bool = False  # Allow re-registration with same key

    # Discovery options
    auto_discover: bool = True  # Auto-import modules to trigger decorators
    discovery_paths: list[str] = field(default_factory=list)  # Paths to search

    # Logging
    log_registration: bool = True  # Log component registration


# ============================================================================
# Base Registry (Abstract)
# ============================================================================


class BaseRegistry[K, T](ABC):
    """
    Abstract base class for all registries.

    Provides common functionality for registration, retrieval, filtering,
    database synchronization, and lifecycle management.

    Subclasses must implement:
        - _validate_component(): Validate before registration
        - _extract_key(): Extract unique key from component
        - _create_instance(): Create instance from component
        - _sync_to_database(): Sync to a database (optional)

    Example:
        class ToolRegistry[str, type[BaseTool]](BaseRegistry):
            def _validate_component(self, tool_class):
                tool_class._validate_metadata()

            def _extract_key(self, tool_class):
                return tool_class.METADATA.name
    """

    def __init__(self, config: RegistryConfig | None = None):
        """Initialize registry with configuration."""
        self.config = config or RegistryConfig()
        self.logger = logging.getLogger(self.__class__.__name__)

        # Core storage
        self._registry: dict[K, T] = {}  # Component classes or metadata
        self._instances: dict[K, Any] = {}  # Cached instances
        self._metadata: dict[K, dict[str, Any]] = {}  # Additional metadata

        # Lifecycle hooks
        self._on_register_hooks: list[Callable[[K, T], None]] = []
        self._on_retrieve_hooks: list[Callable[[K, Any], None]] = []

    # ==================== Abstract Methods (Must Implement) ====================

    @abstractmethod
    def _validate_component(self, component: T) -> None:
        """Validate component before registration."""
        pass

    @abstractmethod
    def _extract_key(self, component: T) -> K:
        """Extract unique key from component."""
        pass

    @abstractmethod
    def _create_instance(self, key: K, component: T, **kwargs) -> Any:
        """Create instance from a component."""
        pass

    @abstractmethod
    async def _sync_to_database(self, db: AsyncSession) -> None:
        """Sync registry to a database (optional, override if needed)."""
        pass

    # ==================== Core Registry Methods ====================

    def register(
        self,
        component: T,
        key: K | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """Register a component in the registry."""
        # Extract key
        if key is None:
            key = self._extract_key(component)

        # Check for duplicates
        if key in self._registry and not self.config.allow_override:
            raise ValueError(
                f"{self.__class__.__name__}: Component '{key}' already registered"
            )

        # Validate component
        if self.config.validate_on_register:
            self._validate_component(component)

        # Register component
        self._registry[key] = component
        if metadata:
            self._metadata[key] = metadata

        # Log registration
        if self.config.log_registration:
            self.logger.info(f"Registered component: {key}")

        # Trigger hooks
        for hook in self._on_register_hooks:
            hook(key, component)

        return component

    def get(self, key: K) -> T | None:
        """Get component by key."""
        return self._registry.get(key)

    def get_instance(self, key: K, **kwargs) -> Any:
        """Get instance of component (with optional caching)."""
        if key not in self._registry:
            raise KeyError(f"Component '{key}' not found in {self.__class__.__name__}")

        # Check cache
        if self.config.enable_instance_cache and key in self._instances:
            instance = self._instances[key]
        else:
            # Create new instance
            component = self._registry[key]
            instance = self._create_instance(key, component, **kwargs)

            # Cache instance
            if self.config.enable_instance_cache:
                self._instances[key] = instance

        # Trigger hooks
        for hook in self._on_retrieve_hooks:
            hook(key, instance)

        return instance

    def list_keys(self) -> list[K]:
        """List all registered keys."""
        return list(self._registry.keys())

    def list_components(self) -> list[T]:
        """List all registered components."""
        return list(self._registry.values())

    def is_registered(self, key: K) -> bool:
        """Check if component is registered."""
        return key in self._registry

    def unregister(self, key: K) -> None:
        """Unregister a component."""
        if key in self._registry:
            del self._registry[key]
        if key in self._instances:
            del self._instances[key]
        if key in self._metadata:
            del self._metadata[key]
        self.logger.info(f"Unregistered component: {key}")

    def clear(self) -> None:
        """Clear all registered components (useful for testing)."""
        self._registry.clear()
        self._instances.clear()
        self._metadata.clear()
        self.logger.info("Registry cleared")

    def filter(self, predicate: Callable[[K, T], bool]) -> dict[K, T]:
        """Filter components by predicate."""
        return {k: v for k, v in self._registry.items() if predicate(k, v)}

    def get_metadata(self, key: K) -> dict[str, Any] | None:
        """Get metadata for a component."""
        return self._metadata.get(key)

    def add_on_register_hook(self, hook: Callable[[K, T], None]) -> None:
        """Add hook to be called when component is registered."""
        self._on_register_hooks.append(hook)

    def add_on_retrieve_hook(self, hook: Callable[[K, Any], None]) -> None:
        """Add hook to be called when instance is retrieved."""
        self._on_retrieve_hooks.append(hook)

    async def sync_to_database(self, db: AsyncSession) -> None:
        """Sync registry state to database."""
        if not self.config.enable_db_sync:
            self.logger.debug("Database sync disabled")
            return

        self.logger.info("Syncing registry to database...")
        await self._sync_to_database(db)
        self.logger.info("Database sync completed")

    def get_statistics(self) -> dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_components": len(self._registry),
            "cached_instances": len(self._instances),
            "metadata_entries": len(self._metadata),
            "config": {
                "enable_db_sync": self.config.enable_db_sync,
                "enable_instance_cache": self.config.enable_instance_cache,
                "allow_override": self.config.allow_override,
            },
        }

    def __len__(self) -> int:
        """Return number of registered components."""
        return len(self._registry)

    def __contains__(self, key: K) -> bool:
        """Check if key is registered."""
        return key in self._registry

    def __repr__(self) -> str:
        """String representation."""
        return f"{self.__class__.__name__}(components={len(self._registry)})"


# ============================================================================
# Tool Registry (Concrete)
# ============================================================================


class ToolRegistry(BaseRegistry[str, type[BaseTool]]):
    """
    Registry for managing tool classes.

    Handles InnerTools (code-defined) and ExternalTools (user-defined).
    Provides filtering by execution mode, category, tags, and schema generation.
    """

    def __init__(self, config: RegistryConfig | None = None):
        """Initialize tool registry with default config."""
        if config is None:
            config = RegistryConfig(
                enable_db_sync=True,  # Sync InnerTools to database
                enable_instance_cache=True,  # Cache tool instances (singleton)
                validate_on_register=True,
                allow_override=False,
                log_registration=True,
            )
        super().__init__(config)

    def _validate_component(self, tool_class: type[BaseTool]) -> None:  # ty:ignore[invalid-method-override]
        """Validate tool class before registration."""
        tool_class._validate_metadata()

    def _extract_key(self, tool_class: type[BaseTool]) -> str:  # ty:ignore[invalid-method-override]
        """Extract tool name from tool class."""
        return tool_class.METADATA.name

    def _create_instance(
        self, key: str, tool_class: type[BaseTool], **kwargs
    ) -> BaseTool:  # ty:ignore[invalid-method-override]
        """Create a tool instance."""
        return tool_class()

    async def _sync_to_database(self, db: AsyncSession) -> None:
        """Sync InnerTools to a database."""
        from aiwen.models.context.tools import Tool as ToolModel
        from aiwen.services.context.tools.base_tool import InnerTool

        synced_count = 0
        skipped_count = 0

        for tool_name, tool_class in self._registry.items():
            # Only sync InnerTools
            if not issubclass(tool_class, InnerTool):
                skipped_count += 1
                continue

            try:
                # Check if exists in a database
                stmt = select(ToolModel).where(
                    ToolModel.tool_code == tool_name,
                    ToolModel.tool_type == "inner",
                )
                result = await db.execute(stmt)
                existing_tool = result.scalar_one_or_none()

                metadata = tool_class.METADATA

                # Prepare tool data
                # Extract major version number from semantic version string (e.g., "1.0.0" -> 1)
                version_int = 1
                if metadata.version:
                    try:
                        version_int = int(metadata.version.split('.')[0])
                    except (ValueError, AttributeError):
                        version_int = 1

                tool_data = {
                    "tool_code": tool_name,
                    "name": metadata.name,
                    "display_name": metadata.display_name,
                    "tool_type": "inner",
                    "description": metadata.description,
                    "version": version_int,
                    "tags": metadata.tags,
                    "category": metadata.category,
                    "enabled": metadata.enabled,
                    "execution_mode": metadata.execution_mode.value,
                    "timeout": metadata.timeout,
                    "input_schema": tool_class.InputSchema.model_json_schema(),
                    "verified": True,
                    "is_public": True,
                }

                if existing_tool:
                    # Update existing
                    for k, v in tool_data.items():
                        setattr(existing_tool, k, v)
                else:
                    # Create new
                    new_tool = ToolModel(**tool_data)
                    db.add(new_tool)

                synced_count += 1

            except Exception as e:
                self.logger.error(f"Failed to sync tool '{tool_name}': {e}")

        await db.commit()
        self.logger.info(f"Tool sync: {synced_count} synced, {skipped_count} skipped")

    # ==================== Tool-Specific Methods ====================

    def _list_tools_instance(
        self,
        execution_mode: ToolExecutionMode|None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[str]:
        """List tool names with optional filters."""

        def predicate(name: str, tool_class: type[BaseTool]) -> bool:
            metadata = tool_class.METADATA
            if execution_mode and metadata.execution_mode != execution_mode:
                return False
            if category and metadata.category != category:
                return False
            return not (enabled_only and not metadata.enabled)

        filtered = self.filter(predicate)
        return sorted(filtered.keys())

    def list_tool_classes(
        self,
        execution_mode: ToolExecutionMode|None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[type[BaseTool]]:
        """List tool classes with optional filters."""
        tool_names = self._list_tools_instance(execution_mode, category, enabled_only)
        return [self._registry[name] for name in tool_names]

    def get_tools_by_tag(self, tag: str) -> list[str]:
        """Query tools by tag."""

        def predicate(name: str, tool_class: type[BaseTool]) -> bool:
            return tag in tool_class.METADATA.tags

        filtered = self.filter(predicate)
        return sorted(filtered.keys())

    def get_all_schemas(self, format: str = "openai") -> list[dict]:
        """Get schemas of all enabled tools."""
        schemas = []
        for tool_class in self.list_tool_classes(enabled_only=True):
            if format == "openai":
                schema = tool_class.get_json_schema()
            elif format == "langchain":
                schema = tool_class.get_langchain_schema()
            else:
                raise ValueError(f"Unsupported format: {format}")
            schemas.append(schema)
        return schemas

    def get_tool_info(self, tool_name: str) -> dict | None:
        """Get detailed tool information."""
        tool_class = self.get(tool_name)
        if not tool_class:
            return None

        metadata = tool_class.METADATA
        return {
            "name": metadata.name,
            "display_name": metadata.display_name,
            "description": metadata.description,
            "version": metadata.version,
            "author": metadata.author,
            "tags": metadata.tags,
            "category": metadata.category,
            "enabled": metadata.enabled,
            "execution_mode": metadata.execution_mode.value,
            "timeout": metadata.timeout,
            "class_name": tool_class.__name__,
            "input_schema": tool_class.InputSchema.model_json_schema(),
            "output_schema": tool_class.OutputSchema.model_json_schema(),
        }

    # ==================== Backward Compatibility ====================

    @classmethod
    def get_tool_class(cls, tool_name: str) -> type[BaseTool] | None:
        """Get tool class (backward compatibility)."""
        instance = cls._get_singleton_instance()
        return instance.get(tool_name)

    @classmethod
    def get_tool_instance(cls, tool_name: str) -> BaseTool | None:
        """Get tool instance (backward compatibility)."""
        instance = cls._get_singleton_instance()
        try:
            return instance.get_instance(tool_name)
        except KeyError:
            return None

    @classmethod
    def _get_singleton_instance(cls) -> ToolRegistry:
        """Get singleton instance of ToolRegistry."""
        from aiwen.registries.manager import RegistryManager

        manager = RegistryManager.get_instance()
        if not manager.is_registered(cls):
            manager.register_registry(cls)
        return manager.get_registry(cls)

    # ==================== Backward Compatibility Class Methods ====================

    @classmethod
    def list_tools(  # type: ignore[misc]
        cls,
        execution_mode: ToolExecutionMode | None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[str]:
        """List tool names (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        return instance._list_tools_instance(execution_mode=execution_mode, category=category, enabled_only=enabled_only)

    @classmethod
    def get_tool_class(cls, tool_name: str) -> type[BaseTool] | None:  # type: ignore[misc]
        """Get tool class by name (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        return BaseRegistry.get(instance, tool_name)

    @classmethod
    def get_tool_instance(cls, tool_name: str, **kwargs) -> BaseTool | None:  # type: ignore[misc]
        """Get tool instance by name (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        return BaseRegistry.get_instance(instance, tool_name, **kwargs)

    @classmethod
    def get_tool_info(cls, tool_name: str) -> dict | None:  # type: ignore[misc]
        """Get tool info by name (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        tool_class = BaseRegistry.get(instance, tool_name)
        if not tool_class:
            return None
        metadata = tool_class.METADATA
        return {
            "name": metadata.name,
            "display_name": metadata.display_name,
            "description": metadata.description,
            "category": metadata.category,
            "execution_mode": metadata.execution_mode.value,
            "enabled": metadata.enabled,
            "version": metadata.version,
        }

    @classmethod
    def get_all_schemas(cls, format: str = "openai") -> list[dict]:  # type: ignore[misc]
        """Get all tool schemas (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        schemas = []
        # Use the instance method directly to avoid recursion
        for tool_name in instance._list_tools_instance(enabled_only=True):
            tool_class = BaseRegistry.get(instance, tool_name)
            if tool_class:
                if format == "openai":
                    schema = tool_class.to_openai_schema()
                elif format == "langchain":
                    schema = tool_class.to_langchain_schema()
                else:
                    raise ValueError(f"Unsupported format: {format}")
                schemas.append(schema)
        return schemas

    @classmethod
    def register(cls, tool_class: type[BaseTool]) -> type[BaseTool]:  # type: ignore[misc]
        """Register a tool class (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        # Call BaseRegistry.register to avoid recursion
        return BaseRegistry.register(instance, tool_class)

    @classmethod
    def unregister(cls, tool_name: str) -> bool:  # type: ignore[misc]
        """Unregister a tool (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        # Call BaseRegistry.unregister to avoid recursion
        BaseRegistry.unregister(instance, tool_name)
        return True

    def get_statistics(self) -> dict:
        """Get detailed registry statistics."""

        base_stats = super().get_statistics()
        total = len(self._registry)
        enabled = len([t for t in self._registry.values() if t.METADATA.enabled])

        # Group by execution mode
        mode_stats = {}
        for mode in ToolExecutionMode:
            count = len(self._list_tools_instance(execution_mode=mode, enabled_only=False))
            mode_stats[mode.value] = count

        # Group by category
        category_stats = {}
        for tool_class in self._registry.values():
            category = tool_class.METADATA.category
            category_stats[category] = category_stats.get(category, 0) + 1

        return {
            **base_stats,
            "total_tools": total,
            "enabled_tools": enabled,
            "disabled_tools": total - enabled,
            "by_execution_mode": mode_stats,
            "by_category": category_stats,
        }


# ============================================================================
# Executor Registry (Concrete)
# ============================================================================


class ExecutorRegistry(BaseRegistry[str, type["Executor"]]):
    """
    Registry for managing agent executor templates.

    Handles dual persistence (in-memory + database) with soft delete support.
    """

    def __init__(self, config: RegistryConfig | None = None):
        """Initialize executor registry with default config."""
        if config is None:
            config = RegistryConfig(
                enable_db_sync=True,  # Sync to database
                enable_instance_cache=False,  # Executors instantiated per run
                validate_on_register=True,
                allow_override=False,
                log_registration=True,
            )
        super().__init__(config)

    def _validate_component(self, executor_cls: type[Executor]) -> None:  # ty:ignore[invalid-method-override]
        """Validate executor class before registration."""
        if not hasattr(executor_cls, "TEMPLATE"):
            raise ValueError(
                f"Executor class {executor_cls.__name__} must define a TEMPLATE attribute"
            )

        template = executor_cls.TEMPLATE
        required_fields = ["template_code", "template_name"]
        missing = [f for f in required_fields if f not in template]
        if missing:
            raise ValueError(
                f"Executor {executor_cls.__name__} TEMPLATE missing fields: {missing}"
            )

    def _extract_key(self, executor_cls: Executor) -> str:  # ty:ignore[invalid-method-override]
        """Extract template code from the executor class."""
        return executor_cls.TEMPLATE["template_code"]

    def _create_instance(
        self, key: str, executor_cls: type[Executor], **kwargs
    ) -> Executor:  # ty:ignore[invalid-method-override]
        """Create executor instance."""
        return executor_cls(**kwargs)

    async def _sync_to_database(self, db: AsyncSession) -> None:
        """Sync executor templates to database."""
        from aiwen.services.executor.executor_template_crud import ExecutorCRUD

        def _normalize_config(config):
            if config is None:
                return {}
            if hasattr(config, "model_dump"):
                return config.model_dump()
            if hasattr(config, "dict"):
                return config.dict()
            if isinstance(config, dict):
                return config
            return {}

        crud = ExecutorCRUD(db)
        synced_count = 0
        failed_count = 0
        deleted_count = 0

        # Sync registered executors
        for executor_code, executor_cls in self._registry.items():
            try:
                template = executor_cls.TEMPLATE
                existing = await crud.get_executor_by_code(executor_code)

                if not existing:
                    await crud.create_executor(
                        executor_code=executor_code,
                        executor_name=template["template_name"],
                        config=_normalize_config(template.get("config")),
                        enabled=template.get("enabled", True),
                        version=template.get("version", 1),
                        auto_commit=False,
                    )
                    self.logger.info(f"✓ Created: {executor_code}")
                    synced_count += 1
                else:
                    if not existing.enabled:
                        existing.enabled = True
                        self.logger.info(f"✓ Re-enabled: {executor_code}")
                    synced_count += 1

            except Exception as e:
                self.logger.error(f"Failed to sync {executor_code}: {e}")
                failed_count += 1

        # Mark unregistered executors as deleted
        try:
            all_db_executors = await crud.list_executors(include_disabled=False)
            registered_codes = set(self._registry.keys())

            for db_executor in all_db_executors:
                if db_executor.executor_code not in registered_codes:
                    await crud.mark_executor_as_deleted(
                        str(db_executor.executor_code), auto_commit=False
                    )
                    self.logger.warning(
                        f"⚠ Marked as deleted: {db_executor.executor_code}"
                    )
                    deleted_count += 1

        except Exception as e:
            self.logger.error(f"Failed to check orphaned executors: {e}")

        await db.commit()
        self.logger.info(
            f"Executor sync: {synced_count} synced, {deleted_count} deleted, {failed_count} failed"
        )

    # ==================== Executor-Specific Methods ====================

    def list_templates(self) -> list[str]:  # ty:ignore[invalid-type-form]
        """List all registered template codes."""
        return sorted(self.list_keys())

    async def register_with_db(
        self,
        executor_code: str,
        executor_name: str,
        executor_cls: type[Executor],
        db: AsyncSession,
        config: dict | None = None,
        enabled: bool = True,
        version: int = 1,
    ):
        """Register executor in both memory and database."""
        if not executor_code or not executor_code.strip():
            raise ValueError("template_code cannot be empty")
        if not executor_name or not executor_name.strip():
            raise ValueError("template_name cannot be empty")

        # Register in memory
        self.register(executor_cls)

        # Register in database
        from aiwen.services.executor.executor_template_crud import ExecutorCRUD

        crud = ExecutorCRUD(db)
        existing = await crud.get_executor_by_code(executor_code)

        if not existing:
            executor = await crud.create_executor(
                executor_code=executor_code,
                executor_name=executor_name,
                config=config if config else {},
                enabled=enabled,
                version=version,
                auto_commit=False,
            )
            self.logger.info(f"✓ Registered: {executor_code = } {executor.id = }")
            return executor

        self.logger.info(f"Executor exists: {executor_code} (id: {existing.id})")
        return existing

    @staticmethod
    def discover_and_import_executors() -> None:
        """
        Import all executor modules to trigger @register_executor decorators.

        Auto-discovers Python files in executor_template/ directory and imports them,
        ensuring their @register_executor decorators execute and register the classes.

        This is a static method to maintain compatibility with bootstrap code.
        """
        import importlib
        from pathlib import Path

        logger.info("Auto-discovering executor modules...")

        # Get executor_template directory
        current_dir = Path(__file__).parent.parent / "services" / "executor"
        executor_template_dir = current_dir / "executor_template"

        if not executor_template_dir.exists():
            logger.warning(
                f"Executor template directory not found: {executor_template_dir}"
            )
            return

        imported_count = 0
        failed_count = 0

        # Recursively find all Python files
        for py_file in executor_template_dir.rglob("*.py"):
            if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
                continue

            try:
                # Convert file path to a module path
                # e.g., executor_template/default/concrete.py -> executor_template.default.concrete
                relative_path = py_file.relative_to(current_dir)
                module_parts = list(relative_path.parts[:-1]) + [py_file.stem]
                module_name = f"aiwen.services.executor.{'.'.join(module_parts)}"

                # Import the module
                importlib.import_module(module_name)
                logger.debug(f"✓ Imported: {module_name}")
                imported_count += 1

            except Exception as e:
                logger.error(f"Failed to import {py_file}: {e}", exc_info=True)
                failed_count += 1

        logger.info(
            f"Executor discovery: {imported_count} imported, {failed_count} failed"
        )

    # ==================== Backward Compatibility ====================

    @classmethod
    def list(cls) -> list[str]:  # ty:ignore[invalid-type-form]
        """List all template codes (backward compatibility)."""
        instance = cls._get_singleton_instance()
        return instance.list_templates()

    @classmethod
    def _get_singleton_instance(cls) -> ExecutorRegistry:
        """Get a singleton instance."""
        from aiwen.registries.manager import RegistryManager

        manager = RegistryManager.get_instance()
        if not manager.is_registered(cls):
            manager.register_registry(cls)
        return manager.get_registry(cls)

    def get_statistics(self) -> dict:
        """Get detailed registry statistics."""
        base_stats = super().get_statistics()
        enabled_count = len(
            [
                cls
                for cls in self._registry.values()
                if cls.TEMPLATE.get("enabled", True)
            ]
        )

        return {
            **base_stats,
            "total_templates": len(self._registry),
            "enabled_templates": enabled_count,
            "disabled_templates": len(self._registry) - enabled_count,
            "template_codes": self.list_templates(),
        }


# ============================================================================
# Decorators
# ============================================================================


def register_tool(tool_class: type[BaseTool]) -> type[BaseTool]:
    """Decorator to register tool class."""
    registry = ToolRegistry._get_singleton_instance()
    return registry.register(tool_class)


def register_executor(executor_cls: type[Executor]) -> type[Executor]:
    """Decorator to register executor class."""
    registry = ExecutorRegistry._get_singleton_instance()
    return registry.register(executor_cls)
