"""
FastAPI 数据库配置
SQLAlchemy 2.0+ 同步引擎 + Session
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import Config

engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    pool_size=Config.SQLALCHEMY_ENGINE_OPTIONS.get('pool_size', 10),
    max_overflow=Config.SQLALCHEMY_ENGINE_OPTIONS.get('max_overflow', 20),
    pool_recycle=Config.SQLALCHEMY_ENGINE_OPTIONS.get('pool_recycle', 3600),
    pool_pre_ping=Config.SQLALCHEMY_ENGINE_OPTIONS.get('pool_pre_ping', True),
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """数据库初始化。

    表结构由 Alembic 迁移管理（见 alembic/ 目录），应用启动时不再
    调用 create_all() —— 那样只能建新表、无法 ALTER 已有表，会导致
    schema 漂移（如 is_email_verified 缺列事故）。

    迁移执行流程（纯手动）：
      - 改模型后：alembic revision -m "xxx" --autogenerate
      - 审查生成脚本 → alembic upgrade head
      - 新环境部署：alembic upgrade head

    本函数保留为空壳，供未来放置其他启动期 DB 初始化逻辑
    （如预热连接、写入初始角色数据等）。
    """
    pass
