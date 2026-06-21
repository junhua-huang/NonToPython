import os
from datetime import timedelta

class ProductionConfig:
    """生产环境配置"""
    
    # 安全密钥 - 必须从环境变量读取
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required")
    
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
    if not JWT_SECRET_KEY:
        raise ValueError("JWT_SECRET_KEY environment variable is required")
    
    # 数据库配置
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    if not SQLALCHEMY_DATABASE_URI:
        raise ValueError("DATABASE_URL environment variable is required")
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 10
    }
    
    # JWT配置
    JWT_ACCESS_TOKEN_EXPIRES = 3600  # 1小时
    
    # 分页配置
    POSTS_PER_PAGE = 20
    
    # 文件上传配置
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
    ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv'}
    ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS
    
    # 生产环境特定配置
    DEBUG = False
    TESTING = False

    # 极光推送 (JPush) 配置 - Master Secret 仅服务端使用，不可下发到客户端
    JPUSH_APP_KEY = os.environ.get('JPUSH_APP_KEY', '')
    JPUSH_MASTER_SECRET = os.environ.get('JPUSH_MASTER_SECRET', '')
    JPUSH_PRODUCTION = os.environ.get('JPUSH_PRODUCTION', 'true').lower() == 'true'

    # 邮件 (SMTP) 配置
    SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.qq.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_FROM = os.environ.get('SMTP_FROM', '')
    SMTP_USE_SSL = os.environ.get('SMTP_USE_SSL', 'true').lower() == 'true'

    # 邮箱验证码限流配置
    OTP_RATE_LIMIT_PER_EMAIL_60S = 1
    OTP_RATE_LIMIT_PER_EMAIL_1H = 5
    OTP_RATE_LIMIT_PER_IP_1H = 10
    OTP_EXPIRE_MINUTES = 10
    LOGIN_FAIL_THRESHOLD = 5

    # CORS配置
    CORS_HEADERS = 'Content-Type'
