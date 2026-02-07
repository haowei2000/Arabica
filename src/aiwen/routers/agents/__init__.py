"""Agent routers module."""

from fastapi import APIRouter

from aiwen.routers.context.knowledge import router as knowledge_router

from .app import router as app_router
from .conversations import router as conversation_router
from .messages import router as message_router

# Create main agent router
router = APIRouter(prefix="/agent", tags=["agent"])

# Include all sub-routers
router.include_router(app_router)
router.include_router(conversation_router)
router.include_router(knowledge_router)
router.include_router(message_router)

__all__ = ["router"]
