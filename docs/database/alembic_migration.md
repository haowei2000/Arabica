# 使用Alembic管理aiwen数据库和添加默认管理员用户

## 概述

本文档详细介绍了如何使用Alembic迁移工具来管理aiwen数据库的模式变更，并添加默认的管理员用户。Alembic是一个轻量级的数据库迁移工具，用于跟踪和应用数据库模式变更。

## 先决条件

在开始之前，请确保您具备以下条件：

1. 已安装Alembic和相关依赖项
2. 数据库服务器正在运行
3. 正确配置了环境变量

## 环境配置

### 1. 环境变量设置

在运行Alembic命令之前，需要设置以下环境变量：

```bash
# PostgreSQL数据库配置
export POSTGRES__HOST=10.1.2.111
export POSTGRES__PORT=5435
export POSTGRES__USERNAME=postgres
export POSTGRES__PASSWORD=difyai123456
export POSTGRES__AIWEN_DBNAME=structure

# 可选：自定义管理员用户设置
export ADMIN_PASSWORD=admin123
export ADMIN_EMAIL=admin@example.com
```

### 2. 更新Alembic配置

编辑 `src/aiwen/migrations/env.py` 文件以正确配置数据库连接和模型元数据。此文件现在使用与应用程序相同的配置系统：

```python
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote_plus

# 添加源目录到路径以导入应用模块
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import engine_from_config, pool
from alembic import context

# 设置环境变量
os.environ.setdefault("ENV", "development")
os.environ.setdefault("POSTGRES__HOST", "10.1.2.111")
os.environ.setdefault("POSTGRES__PORT", "5435")
os.environ.setdefault("POSTGRES__USERNAME", "postgres")
os.environ.setdefault("POSTGRES__PASSWORD", "difyai123456")
os.environ.setdefault("POSTGRES__AIWEN_DBNAME", "structure")

# 尝试从项目配置获取数据库URL
db_url = None
try:
    from structure.config.factory import get_settings

    settings = get_settings()
    # 从设置中获取aiwen数据库URL
    db_urls = settings.postgres.aiwen_sqlalchemy_bind
    if db_urls:
        # 将异步URL转换为同步URL供Alembic使用
        async_url = list(db_urls.values())[0]
        # 将asyncpg替换为psycopg2以进行同步操作
        db_url = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
except Exception as e:
    print(f"警告: 无法从配置工厂加载设置: {e}")

# 如果配置加载失败，回退到环境变量
if not db_url:
    db_url = (
        f"postgresql+psycopg2://"
        f"{quote_plus(os.environ.get('POSTGRES__USERNAME', 'postgres'))}:{quote_plus(os.environ.get('POSTGRES__PASSWORD', '123456'))}@"
        f"{os.environ.get('POSTGRES__HOST', 'localhost')}:{os.environ.get('POSTGRES__PORT', '5432')}/{os.environ.get('POSTGRES__AIWEN_DBNAME', 'structure')}"
    )

# 导入模型
from structure.extensions.database import get_base
from structure.models.auth.user import User
from structure.models.auth.tenant import Tenant

# Alembic配置对象
config = context.config

# 设置日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 获取元数据
target_metadata = get_base("structure").metadata

# 设置数据库URL
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)
    print(f"使用数据库URL: {db_url.split(':')[0]}:******@{db_url.split('@')[1] if '@' in db_url else db_url}")
```

```python
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote_plus

# 添加源目录到路径以导入应用模块
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import engine_from_config, pool
from alembic import context

# 设置环境变量
os.environ.setdefault("ENV", "development")
os.environ.setdefault("POSTGRES__HOST", "10.1.2.111")
os.environ.setdefault("POSTGRES__PORT", "5435")
os.environ.setdefault("POSTGRES__USERNAME", "postgres")
os.environ.setdefault("POSTGRES__PASSWORD", "difyai123456")
os.environ.setdefault("POSTGRES__AIWEN_DBNAME", "structure")

# 导入模型
from structure.extensions.database import get_base
from structure.models.auth.user import User
from structure.models.auth.tenant import Tenant

# Alembic配置对象
config = context.config

# 设置日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 获取元数据
target_metadata = get_base("structure").metadata

# 动态设置数据库URL
db_url = (
    f"postgresql://"
    f"{quote_plus(os.environ['POSTGRES__USERNAME'])}:{quote_plus(os.environ['POSTGRES__PASSWORD'])}@"
    f"{os.environ['POSTGRES__HOST']}:{os.environ['POSTGRES__PORT']}/{os.environ['POSTGRES__AIWEN_DBNAME']}"
)
config.set_main_option("sqlalchemy.url", db_url)
```

## 数据库迁移步骤

### 1. 创建认证表迁移

生成新的迁移脚本来创建认证相关的表：

```bash
alembic revision -m "create auth tables"
```

编辑生成的迁移文件 (`src/aiwen/migrations/versions/<revision_id>_create_auth_tables.py`)：

```python
"""create auth tables

Revision ID: 39ac54d06f2c
Revises:
Create Date: 2025-12-20 17:06:50.494365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = '39ac54d06f2c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - create auth tables."""
    # 创建auth_tenant表
    op.create_table('auth_tenant',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('auth_tenant_pkey')),
        sa.UniqueConstraint('name', name=op.f('auth_tenant_name_key'))
    )
    op.create_index('ix_auth_tenant_name', 'auth_tenant', ['name'], unique=False)

    # 创建auth_user表
    op.create_table('auth_user',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(length=50), server_default=sa.text("'user'::character varying"), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_superuser', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['auth_tenant.id'], name=op.f('auth_user_tenant_id_fkey')),
        sa.PrimaryKeyConstraint('id', name=op.f('auth_user_pkey')),
        sa.UniqueConstraint('email', name=op.f('auth_user_email_key')),
        sa.UniqueConstraint('phone', name=op.f('auth_user_phone_key')),
        sa.UniqueConstraint('username', name=op.f('auth_user_username_key'))
    )
    op.create_index('ix_auth_user_email', 'auth_user', ['email'], unique=False)
    op.create_index('ix_auth_user_phone', 'auth_user', ['phone'], unique=False)
    op.create_index('ix_auth_user_username', 'auth_user', ['username'], unique=False)


def downgrade() -> None:
    """Downgrade schema - drop auth tables."""
    # 删除auth_user表
    op.drop_index('ix_auth_user_username', table_name='auth_user')
    op.drop_index('ix_auth_user_phone', table_name='auth_user')
    op.drop_index('ix_auth_user_email', table_name='auth_user')
    op.drop_table('auth_user')

    # 删除auth_tenant表
    op.drop_index('ix_auth_tenant_name', table_name='auth_tenant')
    op.drop_table('auth_tenant')
```

### 2. 运行认证表迁移

如果表尚未存在，运行以下命令创建认证表：

```bash
alembic upgrade head
```

如果表已经存在，只需标记当前版本：

```bash
alembic stamp head
```

### 3. 创建默认管理员用户迁移

生成新的迁移脚本来添加默认租户和管理员用户：

```bash
alembic revision -m "add default admin user"
```

编辑生成的迁移文件 (`src/aiwen/migrations/versions/<revision_id>_add_default_admin_user.py`)：

```python
"""add default admin user

Revision ID: 1b9369a6e9a1
Revises: 39ac54d06f2c
Create Date: 2025-12-20 17:09:38.963371

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import os
import bcrypt

# revision identifiers
revision: str = '1b9369a6e9a1'
down_revision: Union[str, Sequence[str], None] = '39ac54d06f2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add default tenant and admin user."""
    # 创建连接
    connection = op.get_bind()

    # 检查默认租户是否存在
    result = connection.execute(
        sa.text("SELECT id FROM auth_tenant WHERE name = :name"),
        {"name": "default"}
    )
    tenant_row = result.fetchone()

    # 如果默认租户不存在，则创建
    if not tenant_row:
        connection.execute(
            sa.text("""
                INSERT INTO auth_tenant (id, name, description, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :name, :description, :is_active, NOW(), NOW())
            """),
            {
                "name": "default",
                "description": "Default tenant",
                "is_active": True
            }
        )
        # 获取租户ID
        result = connection.execute(
            sa.text("SELECT id FROM auth_tenant WHERE name = :name"),
            {"name": "default"}
        )
        tenant_row = result.fetchone()

    tenant_id = tenant_row[0] if tenant_row else None

    # 检查管理员用户是否存在
    result = connection.execute(
        sa.text("SELECT id FROM auth_user WHERE username = :username"),
        {"username": "admin"}
    )
    admin_row = result.fetchone()

    # 如果管理员用户不存在，则创建
    if not admin_row and tenant_id:
        # 从环境变量获取管理员密码或使用默认值
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")

        # 使用bcrypt哈希密码（与应用程序中相同）
        password_bytes = admin_password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(password_bytes, salt).decode('utf-8')

        connection.execute(
            sa.text("""
                INSERT INTO auth_user (
                    id, username, email, password_hash, tenant_id, role, is_active, is_superuser, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), :username, :email, :password_hash, :tenant_id, :role, :is_active, :is_superuser, NOW(), NOW()
                )
            """),
            {
                "username": "admin",
                "email": admin_email,
                "password_hash": hashed_password,
                "tenant_id": tenant_id,
                "role": "admin",
                "is_active": True,
                "is_superuser": True
            }
        )


def downgrade() -> None:
    """Downgrade schema - remove default admin user and tenant if they exist."""
    # 创建连接
    connection = op.get_bind()

    # 删除管理员用户（如果存在）
    connection.execute(
        sa.text("DELETE FROM auth_user WHERE username = :username"),
        {"username": "admin"}
    )

    # 删除默认租户（如果存在且没有其他用户引用它）
    connection.execute(
        sa.text("DELETE FROM auth_tenant WHERE name = :name AND id NOT IN (SELECT DISTINCT tenant_id FROM auth_user WHERE tenant_id IS NOT NULL)"),
        {"name": "default"}
    )
```

### 4. 运行默认管理员用户迁移

运行以下命令添加默认租户和管理员用户：

```bash
alembic upgrade head
```

## 验证结果

要验证默认管理员用户是否已成功创建，请运行以下SQL查询：

```bash
export PGPASSWORD=difyai123456 && psql -h 10.1.2.111 -p 5435 -U postgres -d structure -c "SELECT u.username, u.email, u.role, u.is_superuser, t.name as tenant_name FROM auth_user u JOIN auth_tenant t ON u.tenant_id = t.id WHERE u.username = 'admin';"
```

预期输出：
```
 username |       email       | role  | is_superuser | tenant_name
----------+-------------------+-------+--------------+-------------
 admin    | admin@example.com | admin | t            | default
(1 row)
```

## 常用Alembic命令

| 命令 | 描述 |
|------|------|
| `alembic current` | 查看当前版本 |
| `alembic history` | 查看迁移历史 |
| `alembic upgrade head` | 升级到最新版本 |
| `alembic downgrade -1` | 降级到前一个版本 |
| `alembic revision -m "message"` | 创建新的迁移脚本 |
| `alembic stamp head` | 标记当前版本为最新 |

## 自定义设置

您可以通过设置以下环境变量来自定义管理员用户：

- `ADMIN_PASSWORD`：管理员密码（默认：admin123）
- `ADMIN_EMAIL`：管理员邮箱（默认：admin@example.com）

## 故障排除

### 1. 表已存在错误

如果遇到"relation already exists"错误，请使用以下命令标记当前版本而不实际运行迁移：

```bash
alembic stamp head
```

### 2. 数据库连接问题

确保以下环境变量已正确设置：
- POSTGRES__HOST
- POSTGRES__PORT
- POSTGRES__USERNAME
- POSTGRES__PASSWORD
- POSTGRES__AIWEN_DBNAME

### 3. 权限问题

确保数据库用户具有创建表和插入数据的适当权限。

## OAuth2认证

系统使用OAuth2PasswordBearer进行认证。在API文档中（`/docs`），您可以：

1. 点击右上角的"Authorize"按钮
2. 输入用户名：`admin`
3. 输入密码：`admin123`
4. 点击"Authorize"完成登录

登录后，您可以访问所有受保护的API端点。

## 最佳实践

1. **始终备份数据库**：在运行迁移之前备份数据库
2. **测试迁移**：在生产环境之前在测试环境中测试迁移
3. **版本控制**：将迁移文件提交到版本控制系统
4. **文档化变更**：为每个迁移添加清晰的描述
5. **逐步升级**：对于复杂的变更，考虑将其分解为多个小的迁移

通过遵循这些步骤和最佳实践，您可以有效地使用Alembic来管理aiwen数据库的模式变更，并确保默认管理员用户的正确创建。