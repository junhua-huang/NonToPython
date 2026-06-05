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
    """创建所有表"""
    Base.metadata.create_all(bind=engine)
