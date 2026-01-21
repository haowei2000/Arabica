# 数据库迁移

这个目录包含了使用Alembic管理数据库模式变更的所有迁移文件。

## 目录结构

- `env.py` - Alembic环境配置文件
- `script.py.mako` - 迁移脚本模板
- `versions/` - 包含所有迁移脚本的目录

## 常用命令

```bash
# 查看当前版本
alembic current

# 查看迁移历史
alembic history

# 升级到最新版本
alembic upgrade head

# 降级到前一个版本
alembic downgrade -1

# 创建新的迁移脚本
alembic revision -m "描述变更内容"
```

## 迁移文件

1. `39ac54d06f2c_create_auth_tables.py` - 创建认证表
2. `1b9369a6e9a1_add_default_admin_user.py` - 添加默认管理员用户