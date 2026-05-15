"""Transactional email delivery for auth flows."""

from __future__ import annotations

from html import escape
import logging
from uuid import UUID

import httpx

from structure.config.base import AppSettings
from structure.config.factory import get_settings
from structure.services.auth.token_service import TokenService

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when an email provider rejects a delivery request."""


class EmailService:
    """Send auth-related emails through the configured provider."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        email_config = self.settings.email
        return (
            email_config.provider == "brevo"
            and bool(email_config.brevo_api_key)
            and bool(email_config.from_email)
        )

    async def send_verification_email(
        self,
        *,
        user_id: UUID,
        email: str,
        username: str,
    ) -> bool:
        """Send a verification link to a user email address."""
        if not self.settings.email.verify_email_on_register:
            logger.info("Email verification delivery is disabled")
            return False

        if not self.is_configured:
            logger.warning(
                "Email verification delivery skipped: provider not configured"
            )
            return False

        token = TokenService.create_email_verification_token(
            user_id=user_id,
            email=email,
        )
        verify_url = (
            f"{self.settings.email.app_base_url.rstrip('/')}/verify-email?token={token}"
        )

        await self._send_brevo_verification_email(
            email=email,
            username=username,
            verify_url=verify_url,
        )
        return True

    async def _send_brevo_verification_email(
        self,
        *,
        email: str,
        username: str,
        verify_url: str,
    ) -> None:
        email_config = self.settings.email
        safe_username = escape(username or email)
        safe_verify_url = escape(verify_url, quote=True)
        payload = {
            "sender": {
                "name": email_config.from_name,
                "email": email_config.from_email,
            },
            "to": [{"email": email, "name": username or email}],
            "subject": "Verify your Structure account",
            "htmlContent": f"""
                <p>Hello {safe_username},</p>
                <p>Click the button below to verify your Structure account.</p>
                <p>
                  <a href="{safe_verify_url}"
                     style="display:inline-block;padding:10px 16px;background:#111827;color:#fff;text-decoration:none;border-radius:6px;">
                    Verify email
                  </a>
                </p>
                <p>If the button does not work, copy this link into your browser:</p>
                <p><a href="{safe_verify_url}">{safe_verify_url}</a></p>
            """,
            "textContent": (
                f"Verify your Structure account by opening this link:\n{verify_url}"
            ),
        }
        headers = {
            "accept": "application/json",
            "api-key": email_config.brevo_api_key,
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers=headers,
                json=payload,
            )

        if response.status_code >= 400:
            logger.error(
                "Brevo verification email failed: status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            raise EmailDeliveryError("Failed to send verification email")
