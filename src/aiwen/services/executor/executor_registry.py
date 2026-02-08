"""Agent Registry for managing agent templates.

This module provides a registry system for agent templates with both
in-memory and database persistence.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.agent_template import AgentTemplate
from aiwen.services.executor.base import Executor
from aiwen.services.executor.executor_template_crud import ExecutorCRUD

logger = logging.getLogger(__name__)


def _normalize_config(config: dict | object | None) -> dict:
    if config is None:
        return {}
    if hasattr(config, "model_dump"):
        return config.model_dump()  # ty:ignore[call-non-callable]
    if hasattr(config, "dict"):
        return config.dict()  # type: ignore[no-any-return]
    if isinstance(config, dict):
        return config
    return {}


def register_executor(cls: type[Executor]) -> type[Executor]:
    """
    Class decorator to register an agent template.

    Extracts metadata from the TEMPLATE class attribute and registers
    the class in the in-memory registry immediately at import time.

    Database registration happens later during app startup via sync_registry_to_database().

    Usage:
        @register_agent
        class MyAgentTemplate(Executor):
            TEMPLATE = {
                "template_code": "MY001",
                "template_name": "My Agent",
                "enabled": True,
                "version": 1,
                "config": {}
            }

    Args:
        cls: The agent class to register

    Returns:
        The same class (unmodified)

    Raises:
        ValueError: If TEMPLATE attribute is missing or invalid
    """
    # 1. Validate TEMPLATE attribute exists
    if not hasattr(cls, "TEMPLATE"):
        raise ValueError(
            f"Agent class {cls.__name__} must define a TEMPLATE class attribute. "
            f"Example:\n"
            f"    TEMPLATE = {{\n"
            f"        'template_code': 'CODE001',\n"
            f"        'template_name': 'My Agent',\n"
            f"        'enabled': True,\n"
            f"        'version': 1,\n"
            f"        'config': {{}}\n"
            f"    }}"
        )

    template = cls.TEMPLATE

    # 2. Validate required fields
    required_fields = ["template_code", "template_name"]
    missing = [f for f in required_fields if f not in template]
    if missing:
        raise ValueError(
            f"Agent class {cls.__name__} TEMPLATE missing required fields: {missing}"
        )

    template_code = template["template_code"]

    # 3. Check for duplicates
    if template_code in ExecutorRegistry._registry:
        existing = ExecutorRegistry._registry[template_code]
        raise ValueError(
            f"Template code '{template_code}' is already registered by "
            f"{existing.__name__}. Cannot register {cls.__name__}."
        )

    # 4. Register in memory
    ExecutorRegistry._registry[template_code] = cls
    logger.info(f"✓ Registered agent: {template_code} ({cls.__name__})")

    return cls


class ExecutorRegistry:
    """
    System-level agent registry with in-memory cache and database persistence.

    This registry maintains a mapping of template codes to agent classes
    and ensures templates are persisted in the database.
    """

    _registry: dict[str, type[Executor]] = {}

    @classmethod
    async def register(
        cls,
        template_code: str,
        template_name: str,
        agent_cls: type[Executor],
        db_session: AsyncSession,
        config: dict | None = None,
        enabled: bool = True,
        version: int = 1,
    ) -> AgentTemplate:
        """
        Register an agent template in both memory and database.

        Args:
            template_code: Unique identifier for the template
            template_name: Human-readable name for the template
            agent_cls: The agent class implementing Executor
            db_session: Database session for persistence
            config: Optional configuration dictionary
            enabled: Whether the template is enabled
            version: Template version number

        Returns:
            The created or existing AgentTemplate instance

        Raises:
            ValueError: If template_code or template_name is invalid
        """
        if not template_code or not template_code.strip():
            raise ValueError("template_code cannot be empty")
        if not template_name or not template_name.strip():
            raise ValueError("template_name cannot be empty")

        # Register in memory
        cls._registry[template_code] = agent_cls
        logger.debug(f"Registered agent class in memory: {template_code}")

        # Register in database
        crud = ExecutorCRUD(db_session)
        existing = await crud.get_template_by_code(template_code)

        if not existing:
            template = await crud.create_template(
                template_code=template_code,
                template_name=template_name,
                config=_normalize_config(config),
                enabled=enabled,
                version=version,
            )
            logger.info(
                f"✓ Registered new agent template: {template_code} (id: {template.id})"
            )
            return template
        logger.info(
            f"Agent template already exists: {template_code} (id: {existing.id})"
        )
        return existing

    @classmethod
    def get(cls, template_code: str) -> type[Executor]:
        """
        Get an agent class by template code.

        Args:
            template_code: The template code to look up

        Returns:
            The agent class for the given template code

        Raises:
            ValueError: If template_code is not registered
        """
        if template_code not in cls._registry:
            available = ", ".join(cls._registry.keys()) if cls._registry else "none"
            raise ValueError(
                f"Unknown agent template: '{template_code}'. "
                f"Available templates: {available}"
            )
        return cls._registry[template_code]

    @classmethod
    def list(cls) -> list[str]:
        """
        List all registered template codes.

        Returns:
            List of registered template codes
        """
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, template_code: str) -> bool:
        """
        Check if a template code is registered.

        Args:
            template_code: The template code to check

        Returns:
            True if the template is registered, False otherwise
        """
        return template_code in cls._registry

    @classmethod
    def clear(cls) -> None:
        """Clear all registered templates (mainly for testing)."""
        cls._registry.clear()
        logger.warning("Agent registry cleared")

    @property
    def registry(self):
        return self._registry


async def sync_registry_to_database(db_session: AsyncSession) -> None:
    """
    Synchronize all registered agent classes to the database.

    This function should be called during app startup after all modules
    have been imported and decorators have executed.

    It performs two operations:
    1. Ensures each registered agent template exists in the database
    2. Marks database templates as deleted (enabled=False) if they're no longer registered

    Args:
        db_session: Database session for persistence
    """
    logger.info("=== Synchronizing agent registry to database ===")

    crud = ExecutorCRUD(db_session)
    synced_count = 0
    failed_count = 0
    deleted_count = 0

    # Step 1: Sync registered templates to database
    for template_code, agent_cls in ExecutorRegistry._registry.items():
        try:
            # Extract metadata from TEMPLATE attribute
            template = agent_cls.TEMPLATE

            # Check if already exists in database
            existing = await crud.get_template_by_code(template_code)

            if not existing:
                # Create new database record
                await crud.create_template(
                    template_code=template_code,
                    template_name=template["template_name"],
                    config=_normalize_config(template.get("config")),
                    enabled=template.get("enabled", True),
                    version=template.get("version", 1),
                    auto_commit=False,
                )
                logger.info(f"✓ Created database record for: {template_code}")
                synced_count += 1
            else:
                # Update enabled status if it was previously marked as deleted
                if not existing.enabled:
                    existing.enabled = True
                    logger.info(f"✓ Re-enabled template: {template_code}")
                else:
                    logger.debug(f"Database record already exists: {template_code}")
                synced_count += 1

        except Exception as e:
            logger.error(
                f"Failed to sync template {template_code} to database: {e}",
                exc_info=True,
            )
            failed_count += 1

    # Step 2: Mark unregistered templates as deleted
    try:
        # Get all templates from database
        all_db_templates = await crud.list_templates(include_disabled=False)
        registered_codes = set(ExecutorRegistry._registry.keys())

        # Find templates that exist in DB but not in registry
        for db_template in all_db_templates:
            if db_template.template_code not in registered_codes:
                # Mark as deleted (soft delete via enabled=False)
                await crud.mark_template_as_deleted(
                    db_template.template_code,
                    auto_commit=False
                )
                logger.warning(
                    f"⚠ Marked template as deleted (not in registry): {db_template.template_code}"
                )
                deleted_count += 1

    except Exception as e:
        logger.error(
            f"Failed to check for orphaned templates: {e}",
            exc_info=True,
        )

    # Commit all changes
    try:
        await db_session.commit()
    except Exception as e:
        logger.error(f"Failed to commit registry sync: {e}", exc_info=True)
        await db_session.rollback()
        raise

    logger.info(
        f"=== Registry sync complete: {synced_count} synced, {deleted_count} marked as deleted, {failed_count} failed ==="
    )


def _import_all_executor() -> None:
    """
    Import all executor modules to trigger @register_executor decorators.

    This function automatically discovers and imports all Python modules
    in the executor_template directory and its subdirectories, ensuring
    their @register_executor decorators execute and register the classes.
    """
    import importlib
    import os
    from pathlib import Path

    # Get the executor_template directory path
    current_dir = Path(__file__).parent
    executor_template_dir = current_dir / "executor_template"

    if not executor_template_dir.exists():
        logger.warning(f"Executor template directory not found: {executor_template_dir}")
        return

    imported_count = 0
    failed_count = 0

    # Recursively find all Python files in executor_template directory
    for py_file in executor_template_dir.rglob("*.py"):
        # Skip __pycache__ and __init__.py files
        if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
            continue

        try:
            # Convert file path to module path
            # e.g., executor_template/default/concrete.py -> executor_template.default.concrete
            relative_path = py_file.relative_to(current_dir)
            module_parts = list(relative_path.parts[:-1]) + [py_file.stem]
            module_name = f"aiwen.services.executor.{'.'.join(module_parts)}"

            # Import the module
            importlib.import_module(module_name)
            logger.debug(f"✓ Imported executor module: {module_name}")
            imported_count += 1

        except Exception as e:
            logger.error(
                f"Failed to import executor module {py_file}: {e}",
                exc_info=True,
            )
            failed_count += 1

    logger.info(
        f"Executor module import complete: {imported_count} imported, {failed_count} failed"
    )


async def init_executor_registry() -> None:
    """
    Initialize the agent registry during application startup.

    This function:
    1. Imports all agent modules (triggers @register_agent decorators)
    2. Synchronizes the in-memory registry to the database
    3. Verifies registration was successful
    """
    from aiwen.extensions.database import get_session

    logger.info("Starting agent registry initialization...")

    try:
        # Step 1: Import all agent (triggers decorators)
        logger.debug("Importing agent modules...")
        _import_all_executor()

        # Step 2: Verify in-memory registration
        registered = ExecutorRegistry.list()
        logger.debug(f"In-memory registry: {registered}")

        if not registered:
            logger.warning(
                "No agent registered! Check that agent modules are being imported."
            )

        # Step 3: Sync to database
        async with get_session("aiwen") as session:
            logger.info("Synchronizing registry to database...")
            await sync_registry_to_database(session)

        # Step 4: Final verification
        final_count = len(ExecutorRegistry.list())
        logger.info(
            f"Agent registry initialized with {final_count} templates: {ExecutorRegistry.list()}"
        )

    except Exception as e:
        logger.error(f"Failed to initialize agent registry: {e}", exc_info=True)
        raise RuntimeError("Agent registry initialization failed") from e
