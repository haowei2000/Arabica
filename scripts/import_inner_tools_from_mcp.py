import asyncio
import logging
from uuid import UUID

from aiwen.config.factory import get_settings
from aiwen.core.bootstrap import bootstrap_api
from aiwen.extensions.database import get_session
from aiwen.routers.context.tools.tools import MCPImportRequest, MCPToolImport, import_from_mcp
from aiwen.models.auth.user import User
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("import_inner_tools")

async def main():
    # 1. Initialize API bootstrap to get DB and settings
    bootstrap = await bootstrap_api()
    settings = get_settings()
    
    mcp_url = f"http://{settings.mcp_host or 'localhost'}:{settings.mcp_port or 9000}/mcp"
    logger.info(f"Connecting to Aiwen MCP at {mcp_url}")
    
    try:
        async with get_session("aiwen") as session:
            # Get admin user
            result = await session.execute(
                select(User).where(User.username == settings.auth.admin_username)
            )
            admin_user = result.scalar_one_or_none()
            if not admin_user:
                logger.error("Admin user not found. Please run sync-env and start-api first.")
                return

            # Prepare import request
            # We'll first probe to see what's available
            from aiwen.registries.mcp_loader import probe_mcp_server
            client_config = {"mcp_transport": "sse", "mcp_url": mcp_url}
            
            logger.info("Probing MCP server...")
            raw_tools = await probe_mcp_server(mcp_url) # FastMCP probe takes URL
            
            if not raw_tools:
                logger.warning("No tools found on MCP server.")
                return
            
            logger.info(f"Found {len(raw_tools)} tools. Importing...")
            
            tool_names = [t["name"] for t in raw_tools]
            
            request = MCPImportRequest(
                transport="sse",
                url=mcp_url,
                tool_names=tool_names,
                is_public=True
            )
            
            # Use the existing router logic (or call it directly)
            # We need a mock user response for the Depends
            from aiwen.schemas.auth.user_schema import UserResponse
            current_user = UserResponse.model_validate(admin_user)
            
            # Note: import_from_mcp returns MCPImportResponse, not successes/failures list directly
            response = await import_from_mcp(
                body=request,
                current_user=current_user,
                db=session
            )
            
            # MCPImportResponse has imported, skipped, failed (all lists of strings)
            if response.imported:
                logger.info(f"Successfully imported {len(response.imported)} tools.")
                for name in response.imported:
                    logger.info(f"  - {name}")
            
            if response.skipped:
                logger.info(f"Skipped {len(response.skipped)} tools (already exist).")
                
            if response.failed:
                logger.error(f"Failed to import {len(response.failed)} tools.")
                for name in response.failed:
                    logger.error(f"  - {name}")

    finally:
        await bootstrap.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
