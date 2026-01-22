"""Agent routers module."""

from fastapi import APIRouter

from .app import router as app_router
from .chat import router as chat_router
from .conversations import router as conversation_router
from .messages import router as message_router

# Create main agents router
router = APIRouter(prefix="/agents", tags=["agents"])

# Include all sub-routers
router.include_router(app_router)
router.include_router(chat_router)
router.include_router(conversation_router)
router.include_router(message_router)

__all__ = ["router"]
