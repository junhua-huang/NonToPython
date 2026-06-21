"""Alembic 迁移环境配置。

连接串复用 app.core.config.Config.SQLALCHEMY_DATABASE_URI（从 .env 读取），
避免在 alembic.ini 里硬编码数据库密码。模型元数据绑定 app.database.Base.metadata，
所有表定义在 app.models.models，必须显式 import 让它们注册到 metadata。
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── 把项目根加入 sys.path，让 `app.*` 可导入 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── 加载 .env + 复用应用配置 ──
from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from app.core.config import Config  # noqa: E402
from app.database import Base  # noqa: E402
import app.models.models  # noqa: E402,F401  # 注册所有表到 Base.metadata
import app.models.community  # noqa: E402,F401  # 注册社群相关表

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 把应用配置的连接串注入 alembic（覆盖 alembic.ini 里留空的 sqlalchemy.url）
config.set_main_option("sqlalchemy.url", Config.SQLALCHEMY_DATABASE_URI)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 绑定模型元数据，供 autogenerate 对比
target_metadata = Base.metadata


# ── MySQL FK 自动索引白名单 ──
# MySQL InnoDB 为每个 ForeignKey 自动创建同名索引（如 comic_events.city_id），
# 这些索引不在模型里显式声明，autogenerate 会反复报"仅库有"。它们是 FK 必需的，
# 不能删。列出 (table, index_name) 对，让 include_object 跳过。
_FK_AUTO_INDEXES = {
    # comic_*
    ('comic_event_follows', 'event_id'),
    ('comic_event_follows', 'user_id'),
    ('comic_event_images', 'event_id'),
    ('comic_event_tag_rel', 'event_id'),
    ('comic_event_tag_rel', 'tag_id'),
    ('comic_events', 'city_id'),
    ('comic_events', 'creator_id'),
    # role / user
    ('role_applications', 'reviewer_id'),
    ('role_applications', 'role_id'),
    ('user_roles', 'role_id'),
    # post / topic
    ('post_topics', 'topic_id'),
    ('topic_followers', 'topic_id'),
    # ws
    ('ws_ack_dedup', 'user_id'),
    # comment (历史索引，保留用于查询性能)
    ('comments', 'idx_reply_to_user'),
    ('comments', 'ix_comments_created_at'),
}

# 模型声明了 index=True 但 MySQL FK 已自动建了同名索引的表，
# autogenerate 会报"Detected added index ix_xxx"。这些也应跳过。
# 格式：(table, index_name)
_FK_OVERLAP_INDEXES = {
    ('comic_comments', 'ix_comic_comments_event_id'),
    ('comic_comments', 'ix_comic_comments_parent_id'),
    ('comic_comments', 'ix_comic_comments_user_id'),
    ('comic_comments', 'reply_to_user_id'),
    ('comic_comments', 'user_id'),
    ('comic_likes', 'ix_comic_likes_event_id'),
    ('comic_likes', 'ix_comic_likes_user_id'),
    ('comic_likes', 'user_id'),
    ('comments', 'ix_comments_parent_id'),
    ('post_visibility', 'user_id'),
}


def include_object(object, name, type_, reflected, compare_to):
    """autogenerate 过滤器，屏蔽无害噪音。"""
    # 1. 忽略 alembic 自身版本表
    if type_ == "table" and name == "alembic_version":
        return False

    # 2. 忽略 MySQL FK 自动生成的隐式索引
    if type_ == "index":
        table_name = object.table.name if hasattr(object, 'table') else None
        if table_name and (table_name, name) in _FK_AUTO_INDEXES:
            return False
        if table_name and (table_name, name) in _FK_OVERLAP_INDEXES:
            return False

    return True


def include_name(name, type_, parent_names):
    """忽略 alembic_version 表（schema-level 更早期过滤）。"""
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to the script output without connecting to the DB.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
        include_object=include_object,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
            include_object=include_object,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
