"""
NanTuPy - FastAPI 应用配置
"""
import os
from datetime import timedelta

class Config:
    # 安全密钥 - 从环境变量读取
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'change-me-in-production')

    # 数据库配置 - 从环境变量读取
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASS = os.environ.get('DB_PASS', '')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '3306')
    DB_NAME = os.environ.get('DB_NAME', 'facebook')
    SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_size': 10, 'max_overflow': 20, 'pool_recycle': 3600, 'pool_pre_ping': True}

    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    POSTS_PER_PAGE = 20

    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, '..', 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
    ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv'}
    ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS

    # 云存储配置 - 从环境变量读取
    COS_ID = os.environ.get('COS_ID', '')
    COS_KEY = os.environ.get('COS_KEY', '')
    COS_REGION = os.environ.get('COS_REGION', 'ap-guangzhou')
    COS_BUCKET = os.environ.get('COS_BUCKET', '')
    COS_BUCKET_NAME = os.environ.get('COS_BUCKET_NAME', os.environ.get('COS_BUCKET', ''))
    COS_DOMAIN = os.environ.get('COS_DOMAIN', '')

    DEBUG = True
    TESTING = False
    CORS_HEADERS = 'Content-Type'
