# structure/config/components/auth.py
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """
    配置认证相关设置的模型类

    该类定义了JWT认证所需的各种配置参数，包括密钥、算法和令牌过期时间等设置。
    """

    admin_username: str = Field(
        default="admin",
        description="管理员用户名，默认为admin",
    )
    admin_password: str = Field(
        default="admin123",
        description="管理员密码，默认为admin123",
    )
    admin_email: str = Field(
        default="admin@example.com",
        description="管理员邮箱，默认为空",
    )
    jwt_secret_key: str = Field(
        default="your-super-secret-jwt-key-here-change-in-production",
        description="用于JWT令牌签名的密钥，生产环境中必须更换为安全的密钥",
    )

    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT令牌签名使用的算法，默认为HS256",
    )

    access_token_expire_minutes: int = Field(
        default=30,
        description="访问令牌的过期时间（分钟），默认为30分钟",
    )

    refresh_token_expire_days: int = Field(
        default=7,
        description="刷新令牌的过期时间（天），默认为7天",
    )
