# structure/config/components/email.py
from pydantic import BaseModel, Field


class EmailConfig(BaseModel):
    """Email delivery settings."""

    provider: str = Field(default="brevo", description="Email provider name")
    brevo_api_key: str = Field(default="", description="Brevo transactional API key")
    from_name: str = Field(default="Structure", description="Sender display name")
    from_email: str = Field(
        default="noreply@example.com",
        description="Verified sender email address",
    )
    app_base_url: str = Field(
        default="http://localhost:3000",
        description="Public frontend base URL for verification links",
    )
    verify_email_on_register: bool = Field(
        default=True,
        description="Send a verification email after email registration",
    )
    verification_token_expire_minutes: int = Field(
        default=60,
        description="Email verification token lifetime in minutes",
    )
