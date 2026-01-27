"""Agent Registry for managing agent templates.

This module provides a registry system for agent templates with both
in-memory and database persistence.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.agent_template import AgentTemplate
from aiwen.services.agent.base import BaseAgentTemplate
from aiwen.services.crud.agent_template_crud import AgentTemplateCRUD

logger = logging.getLogger(__name__)


def register_agent(cls: type[BaseAgentTemplate]) -> type[BaseAgentTemplate]:
    """
    Class decorator to register an agent template.

    Extracts metadata from the TEMPLATE class attribute and registers
    the class in the in-memory registry immediately at import time.

    Database registration happens later during app startup via sync_registry_to_database().

    Usage:
        @register_agent
        class MyAgentTemplate(BaseAgentTemplate):
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
    if template_code in AgentRegistry._registry:
        existing = AgentRegistry._registry[template_code]
        raise ValueError(
            f"Template code '{template_code}' is already registered by "
            f"{existing.__name__}. Cannot register {cls.__name__}."
        )

    # 4. Register in memory
    AgentRegistry._registry[template_code] = cls
    logger.info(f"✓ Registered agent: {template_code} ({cls.__name__})")

    return cls


class AgentRegistry:
    """
    System-level agent registry with in-memory cache and database persistence.

    This registry maintains a mapping of template codes to agent classes
    and ensures templates are persisted in the database.
    """

    _registry: dict[str, type[BaseAgentTemplate]] = {}

    @classmethod
    async def register(
        cls,
        template_code: str,
        template_name: str,
        agent_cls: type[BaseAgentTemplate],
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
            agent_cls: The agent class implementing BaseAgentTemplate
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
        crud = AgentTemplateCRUD(db_session)
        existing = await crud.get_template_by_code(template_code)

        if not existing:
            template = await crud.create_template(
                template_code=template_code,
                template_name=template_name,
                config=config or {},
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
    def get(cls, template_code: str) -> type[BaseAgentTemplate]:
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

    It iterates through the in-memory registry and ensures each agent
    template exists in the database.

    Args:
        db_session: Database session for persistence
    """
    logger.info("=== Synchronizing agent registry to database ===")

    crud = AgentTemplateCRUD(db_session)
    synced_count = 0
    failed_count = 0

    for template_code, agent_cls in AgentRegistry._registry.items():
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
                    config=template.get("config", {}),
                    enabled=template.get("enabled", True),
                    version=template.get("version", 1),
                )
                logger.info(f"✓ Created database record for: {template_code}")
                synced_count += 1
            else:
                logger.debug(f"Database record already exists: {template_code}")
                synced_count += 1

        except Exception as e:
            logger.error(
                f"Failed to sync template {template_code} to database: {e}",
                exc_info=True,
            )
            failed_count += 1

    logger.info(
        f"=== Registry sync complete: {synced_count} synced, {failed_count} failed ==="
    )


def _import_all_agents() -> None:
    """
    Import all agent modules to trigger @register_agent decorators.

    This function explicitly imports all agent template modules,
    ensuring their decorators execute and register the classes.
    """
    # Import all agent template modules

    # Future agent can be added here
    # from aiwen.services.agent.agent_template.custom.concrete import CustomAgentTemplate
    logger.info("All agent modules imported")


async def init_agent_registry() -> None:
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
        logger.info("Importing agent modules...")
        _import_all_agents()

        # Step 2: Verify in-memory registration
        registered = AgentRegistry.list()
        logger.info(f"In-memory registry: {registered}")

        if not registered:
            logger.warning(
                "No agent registered! Check that agent modules are being imported."
            )

        # Step 3: Sync to database
        async with get_session("aiwen") as session:
            logger.info("Synchronizing registry to database...")
            await sync_registry_to_database(session)

        # Step 4: Final verification
        final_count = len(AgentRegistry.list())
        logger.info(
            f"Agent registry initialized with {final_count} templates: {AgentRegistry.list()}"
        )

    except Exception as e:
        logger.error(f"Failed to initialize agent registry: {e}", exc_info=True)
        raise RuntimeError("Agent registry initialization failed") from e
