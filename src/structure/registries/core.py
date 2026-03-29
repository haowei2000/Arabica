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

from structure.core.interfaces import Executor
from structure.core.interfaces.tool import BaseTool

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
        """Sync InnerTools to the database and enforce consistency.

        Three phases:
          1. **Upsert** – create or update a DB record for every
             registered InnerTool.
          2. **Orphan cleanup** – disable DB inner-tool records whose
             ``tool_code`` no longer exists in the registry (tool was
             removed from code).
          3. **Reference validation** – disable external tools whose
             ``inner_tool_name`` or chain steps point to tools that
             are no longer registered.
        """
        from structure.core.interfaces.tool import InnerTool
        from structure.models.context.tools import Tool as ToolModel

        # Collect the set of currently-registered InnerTool names
        registered_names: set[str] = set()

        # ── Phase 1: Upsert InnerTools ────────────────────────────
        synced_count = 0
        skipped_count = 0
        # Collect names of always_load tools to fire Celery tasks after commit.
        always_load_names: list[str] = []

        for tool_name, tool_class in self._registry.items():
            if not issubclass(tool_class, InnerTool):
                skipped_count += 1
                continue

            registered_names.add(tool_name)

            if getattr(tool_class.METADATA, "always_load", False):
                always_load_names.append(tool_name)

            try:
                stmt = select(ToolModel).where(
                    ToolModel.name == tool_name,
                    ToolModel.tool_type == "inner",
                )
                result = await db.execute(stmt)
                existing_tool = result.scalar_one_or_none()

                metadata = tool_class.METADATA

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
                    "timeout": metadata.timeout,
                    "input_schema": tool_class.InputSchema.model_json_schema(),
                    "verified": True,
                    "is_public": True,
                }

                if existing_tool:
                    for k, v in tool_data.items():
                        setattr(existing_tool, k, v)
                else:
                    new_tool = ToolModel(**tool_data)
                    db.add(new_tool)

                synced_count += 1

            except Exception as e:
                self.logger.error(f"Failed to sync tool '{tool_name}': {e}")

        await db.flush()

        # ── Phase 2: Disable orphaned inner tools ─────────────────
        orphan_count = 0
        try:
            orphan_stmt = select(ToolModel).where(
                ToolModel.tool_type == "inner",
                ToolModel.enabled == True,  # noqa: E712
                ToolModel.tool_code.notin_(registered_names),
            )
            orphan_result = await db.execute(orphan_stmt)
            for orphan in orphan_result.scalars():
                orphan.enabled = False
                orphan_count += 1
                self.logger.warning(
                    "Disabled orphaned inner tool: %s (removed from code)",
                    orphan.tool_code,
                )
        except Exception as e:
            self.logger.error(f"Failed to clean up orphaned inner tools: {e}")

        # ── Phase 3: Disable external tools with broken references ─
        broken_count = 0
        try:
            ext_stmt = select(ToolModel).where(
                ToolModel.tool_type == "external",
                ToolModel.enabled == True,  # noqa: E712
            )
            ext_result = await db.execute(ext_stmt)

            for ext_tool in ext_result.scalars():
                broken_ref = self._find_broken_reference(ext_tool, registered_names)
                if broken_ref:
                    ext_tool.enabled = False
                    broken_count += 1
                    self.logger.warning(
                        "Disabled external tool '%s' (id=%s): %s",
                        ext_tool.name,
                        ext_tool.id,
                        broken_ref,
                    )
        except Exception as e:
            self.logger.error(
                f"Failed to validate external tool references: {e}"
            )

        await db.commit()
        self.logger.info(
            "Tool sync: %d synced, %d skipped, "
            "%d orphaned inner tools disabled, "
            "%d external tools with broken refs disabled",
            synced_count,
            skipped_count,
            orphan_count,
            broken_count,
        )

        # ── Fire Celery tasks for always_load InnerTools ───────────────
        # Re-query IDs after commit so the UUIDs are guaranteed to be persisted.
        if always_load_names:
            try:
                id_result = await db.execute(
                    select(ToolModel.id, ToolModel.name).where(
                        ToolModel.name.in_(always_load_names),
                        ToolModel.tool_type == "inner",
                    )
                )
                id_rows = id_result.all()
                for row in id_rows:
                    self.logger.info(
                        "always_load tool '%s' registered (no workspace-context sync)",
                        row.name,
                    )
            except Exception as e:
                self.logger.error(
                    "Failed to queue always_load tool context sync: %s", e
                )

        # ── Phase 4: Sync inner tool toolsets by category ─────────────
        try:
            from structure.models.context.tools.tool_bundle import ToolBundle, ToolBundleItem
            from sqlalchemy.orm import selectinload

            inner_tools_stmt = select(ToolModel).where(
                ToolModel.tool_type == "inner",
                ToolModel.enabled == True,  # noqa: E712
            )
            inner_tools = (await db.execute(inner_tools_stmt)).scalars().all()

            category_map: dict[str, list] = {}
            for t in inner_tools:
                cat = t.category or "general"
                category_map.setdefault(cat, []).append(t)

            for category, tools in category_map.items():
                bundle_stmt = (
                    select(ToolBundle)
                    .options(selectinload(ToolBundle.items))
                    .where(
                        ToolBundle.bundle_type == "inner",
                        ToolBundle.source == category,
                    )
                )
                bundle = (await db.execute(bundle_stmt)).scalar_one_or_none()
                if not bundle:
                    bundle = ToolBundle(
                        bundle_type="inner",
                        source=category,
                        name=category,
                        description=f"Built-in {category} tools",
                        is_public=True,
                    )
                    db.add(bundle)
                    await db.flush()
                    existing_tool_ids: set = set()  # new bundle, no items yet
                else:
                    existing_tool_ids = {item.tool_id for item in bundle.items}
                for idx, tool in enumerate(tools):
                    if tool.id not in existing_tool_ids:
                        db.add(ToolBundleItem(bundle_id=bundle.id, tool_id=tool.id, position=idx))

            await db.commit()
            self.logger.info("Synced %d inner tool toolsets by category", len(category_map))
        except Exception as e:
            self.logger.error("Failed to sync inner tool toolsets: %s", e)

        # ── Phase 5: Upsert inner tools into context table for all users ─
        # For every registered inner tool × every user: create or update a
        # Context row (context_type="tool", source_id=tool.id, user_id=user.id).
        try:
            import json as _json
            from uuid import uuid4 as _uuid4

            from structure.core.enums import ContextType as _ContextType
            from structure.models.auth.user import User
            from structure.models.context.context import Context
            from structure.utils.context import slugify as _slugify

            # All registered inner tools (no enabled filter — sync all)
            synced_tools = (await db.execute(
                select(ToolModel).where(
                    ToolModel.tool_type == "inner",
                    ToolModel.tool_code.in_(registered_names),
                )
            )).scalars().all()

            all_users = (await db.execute(select(User))).scalars().all()

            self.logger.info(
                "Phase 5: syncing %d inner tools for %d users",
                len(synced_tools), len(all_users),
            )

            if not synced_tools or not all_users:
                self.logger.info("Phase 5: nothing to sync, skipping")
            else:
                tool_ids = [t.id for t in synced_tools]
                user_ids = [u.id for u in all_users]

                # Load all existing (source_id, user_id) pairs in one shot
                existing_pairs: set[tuple] = {
                    (row.source_id, row.user_id)
                    for row in (await db.execute(
                        select(Context.source_id, Context.user_id).where(
                            Context.context_type == _ContextType.TOOL,
                            Context.source_id.in_(tool_ids),
                            Context.user_id.in_(user_ids),
                        )
                    ))
                }

                # Load existing Context objects that need updating
                existing_ctx_map: dict[tuple, Context] = {
                    (ctx.source_id, ctx.user_id): ctx
                    for ctx in (await db.execute(
                        select(Context).where(
                            Context.context_type == _ContextType.TOOL,
                            Context.source_id.in_(tool_ids),
                            Context.user_id.in_(user_ids),
                        )
                    )).scalars().all()
                }

                ctx_created = ctx_updated = 0
                for tool in synced_tools:
                    tool_name = tool.tool_code or tool.name
                    display_name = tool.display_name or tool.name
                    glance = f"{display_name} — {(tool.description or '')[:60]}"
                    content = _json.dumps(
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": tool.description or display_name,
                                "parameters": tool.input_schema or {},
                            },
                        },
                        ensure_ascii=False,
                    )
                    tags = ["tool"] + (tool.tags or [])
                    meta = {"tool_id": str(tool.id), "tool_code": tool_name, "name": display_name}

                    tool_path = f"tools/{_slugify(tool_name)}"
                    for user in all_users:
                        key = (tool.id, user.id)
                        if key in existing_pairs:
                            ctx = existing_ctx_map.get(key)
                            if ctx:
                                ctx.glance = glance
                                ctx.content = content
                                ctx.path = tool_path
                                ctx.tags = tags
                                ctx.meta = {**(ctx.meta or {}), **meta}
                                ctx.embedding_384 = None
                                ctx.embedding_768 = None
                                ctx.embedding_1024 = None
                                ctx.embedding_1536 = None
                            ctx_updated += 1
                        else:
                            db.add(Context(
                                id=_uuid4(),
                                user_id=user.id,
                                context_type=_ContextType.TOOL,
                                source_id=tool.id,
                                glance=glance,
                                path=tool_path,
                                content=content,
                                tags=tags,
                                meta=meta,
                            ))
                            ctx_created += 1

                await db.commit()
                self.logger.info(
                    "Phase 5 done: created=%d updated=%d (%d tools × %d users)",
                    ctx_created, ctx_updated, len(synced_tools), len(all_users),
                )

                # ── Dispatch WorkspaceContext sync + embedding generation ──
                # Phase 5 only writes the Context table rows. The async task
                # handles: (a) syncing tools/{name} into every WorkspaceContext
                # and (b) generating embeddings for the Context rows.
                try:
                    from structure.celery_worker.tasks.context_sync_tasks import (
                        sync_inner_tool_to_contexts,
                    )
                    for tool in synced_tools:
                        sync_inner_tool_to_contexts.delay(str(tool.id))
                    self.logger.info(
                        "Phase 5: dispatched %d sync_inner_tool tasks",
                        len(synced_tools),
                    )
                except Exception as dispatch_err:
                    self.logger.warning(
                        "Phase 5: failed to dispatch inner tool context sync tasks: %s",
                        dispatch_err,
                    )

        except Exception as e:
            self.logger.error(
                "Phase 5 failed — inner tools not synced to context table: %s",
                e, exc_info=True,
            )

    @staticmethod
    def _find_broken_reference(
        tool_record, registered_names: set[str]
    ) -> str | None:
        """Check if an external tool has broken inner-tool references.

        Returns a human-readable reason string if broken, else ``None``.
        """
        # Check single-delegation mode
        inner = getattr(tool_record, "inner_tool_name", None)
        chain = getattr(tool_record, "chain", None)

        if inner and not chain:
            if inner not in registered_names:
                return (
                    f"inner_tool_name '{inner}' is no longer registered"
                )

        # Check chain/pipeline mode
        if chain:
            for idx, step in enumerate(chain):
                step_tool = step.get("tool_name") if isinstance(step, dict) else None
                if step_tool and step_tool not in registered_names:
                    return (
                        f"chain step {idx} references "
                        f"'{step_tool}' which is no longer registered"
                    )

        return None

    # ==================== Tool-Specific Methods ====================

    def _list_tools_instance(
        self,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[str]:
        """List tool names with optional filters."""

        def predicate(name: str, tool_class: type[BaseTool]) -> bool:
            metadata = tool_class.METADATA
            if category and metadata.category != category:
                return False
            return not (enabled_only and not metadata.enabled)

        filtered = self.filter(predicate)
        return sorted(filtered.keys())

    def list_tool_classes(
        self,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[type[BaseTool]]:
        """List tool classes with optional filters."""
        tool_names = self._list_tools_instance(category, enabled_only)
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
            "timeout": metadata.timeout,
            "class_name": tool_class.__name__,
            "input_schema": tool_class.InputSchema.model_json_schema(),
            "output_schema": tool_class.OutputSchema.model_json_schema(),
        }

    # ==================== Auto-Discovery ====================

    def discover_and_register_tools(
        self, extra_paths: list[str] | None = None
    ) -> None:
        """Auto-discover and register all InnerTool subclasses from plugins/tools/.

        Recursively scans all Python files under ``structure/plugins/tools/``,
        finds concrete ``BaseTool`` subclasses that define ``METADATA``, and
        registers them in this registry.
        """
        import importlib
        import inspect
        from pathlib import Path

        plugins_dir = Path(__file__).parent.parent / "plugins"
        tools_dir = plugins_dir / "tools"

        # Collect module paths by scanning the directory
        paths: list[str] = []
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
                    continue
                relative_path = py_file.relative_to(plugins_dir)
                module_parts = list(relative_path.parts[:-1]) + [py_file.stem]
                paths.append(f"structure.plugins.{'.'.join(module_parts)}")
        else:
            self.logger.warning("Tool plugins directory not found: %s", tools_dir)

        if extra_paths:
            paths.extend(extra_paths)

        registered = 0
        for module_path in paths:
            try:
                mod = importlib.import_module(module_path)
            except Exception as e:
                self.logger.error(
                    "Failed to import tool module %s: %s", module_path, e
                )
                continue

            for _name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, BaseTool)
                    and obj is not BaseTool
                    and not inspect.isabstract(obj)
                    and hasattr(obj, "METADATA")
                    and obj.METADATA.name not in self._registry
                ):
                    try:
                        self.register(obj)
                        registered += 1
                    except Exception as e:
                        self.logger.error(
                            "Failed to register tool %s: %s", obj.__name__, e
                        )

        self.logger.info(
            "Tool auto-discovery: %d tools registered from %d modules",
            registered,
            len(paths),
        )

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
        from structure.registries.manager import RegistryManager

        manager = RegistryManager.get_instance()
        if not manager.is_registered(cls):
            manager.register_registry(cls)
        return manager.get_registry(cls)

    # ==================== Backward Compatibility Class Methods ====================

    @classmethod
    def list_tools(  # type: ignore[misc]
        cls,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[str]:
        """List tool names (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        return instance._list_tools_instance(category=category, enabled_only=enabled_only)

    @classmethod
    def get_tool_class(cls, tool_name: str) -> type[BaseTool] | None:  # type: ignore[misc]
        """Get tool class by name (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        return BaseRegistry.get(instance, tool_name)

    @classmethod
    def get_tool_instance(cls, tool_name: str, **kwargs) -> BaseTool | None:  # type: ignore[misc]
        """Get tool instance by name (backward compatibility class method)."""
        instance = cls._get_singleton_instance()
        try:
            return BaseRegistry.get_instance(instance, tool_name, **kwargs)
        except KeyError:
            return None

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

        # Group by category
        category_stats: dict[str, int] = {}
        for tool_class in self._registry.values():
            category = tool_class.METADATA.category
            category_stats[category] = category_stats.get(category, 0) + 1

        return {
            **base_stats,
            "total_tools": total,
            "enabled_tools": enabled,
            "disabled_tools": total - enabled,
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
        required_fields = ["executor_code", "executor_name"]
        missing = [f for f in required_fields if f not in template]
        if missing:
            raise ValueError(
                f"Executor {executor_cls.__name__} TEMPLATE missing fields: {missing}"
            )

    def _extract_key(self, executor_cls: Executor) -> str:  # ty:ignore[invalid-method-override]
        """Extract template code from the executor class."""
        return executor_cls.TEMPLATE["executor_code"]

    def _create_instance(
        self, key: str, executor_cls: type[Executor], **kwargs
    ) -> Executor:  # ty:ignore[invalid-method-override]
        """Create executor instance."""
        return executor_cls(**kwargs)

    async def _sync_to_database(self, db: AsyncSession) -> None:
        """Sync executor templates to database."""
        from structure.services.executor.executor_crud import ExecutorCRUD

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
                        executor_name=template["executor_name"],
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
            raise ValueError("executor_code cannot be empty")
        if not executor_name or not executor_name.strip():
            raise ValueError("executor_name cannot be empty")

        # Register in memory
        self.register(executor_cls)

        # Register in database
        from structure.services.executor.executor_crud import ExecutorCRUD

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
        """Auto-discover and register all Executor subclasses in plugins/executors/.

        Scans all Python files, imports them, then inspects every exported
        class. Any concrete Executor subclass that defines a TEMPLATE dict
        is automatically registered — no @register_executor decorator needed.
        """
        import importlib
        import inspect
        from pathlib import Path

        from structure.core.interfaces import Executor

        logger.info("Auto-discovering executor modules...")

        plugins_dir = Path(__file__).parent.parent / "plugins"
        executors_dir = plugins_dir / "executors"

        if not executors_dir.exists():
            logger.warning(
                f"Executor plugins directory not found: {executors_dir}"
            )
            return

        registry = ExecutorRegistry._get_singleton_instance()
        imported_count = 0
        registered_count = 0
        failed_count = 0

        for py_file in executors_dir.rglob("*.py"):
            if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
                continue

            try:
                relative_path = py_file.relative_to(plugins_dir)
                module_parts = list(relative_path.parts[:-1]) + [py_file.stem]
                module_name = f"structure.plugins.{'.'.join(module_parts)}"

                module = importlib.import_module(module_name)
                logger.debug(f"Imported: {module_name}")
                imported_count += 1

                # Scan module for Executor subclasses and register them
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, Executor)
                        and obj is not Executor
                        and hasattr(obj, "TEMPLATE")
                        and isinstance(obj.TEMPLATE, dict)
                        and "executor_code" in obj.TEMPLATE
                    ):
                        code = obj.TEMPLATE["executor_code"]
                        if registry.get(code) is None:
                            registry.register(obj)
                            registered_count += 1
                            logger.debug(f"Registered executor: {code}")

            except Exception as e:
                logger.error(f"Failed to import {py_file}: {e}", exc_info=True)
                failed_count += 1

        logger.info(
            f"Executor discovery: {imported_count} imported, "
            f"{registered_count} registered, {failed_count} failed"
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
        from structure.registries.manager import RegistryManager

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
            "executor_codes": self.list_templates(),
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
