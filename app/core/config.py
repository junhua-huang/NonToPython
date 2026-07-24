"""
NanTuPy - FastAPI 应用配置
"""
import os
from datetime import timedelta


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw_value = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


class Config:
    # 安全密钥 - 从环境变量读取
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'change-me-in-production')

    # 运行环境与 CORS 配置
    # 开发环境完全放开 HTTP/HTTPS 来源，生产环境默认只允许正式 www 前端域名。
    DEFAULT_PRODUCTION_CORS_ORIGINS = [
        'https://www.nonto.online',
    ]
    DEVELOPMENT_CORS_ORIGIN_REGEX = r'^https?://.+$'

    @classmethod
    def get_app_env(cls) -> str:
        return os.environ.get('APP_ENV', 'development').strip().lower()

    @classmethod
    def get_cors_settings(cls) -> dict:
        origins_raw = os.environ.get('CORS_ORIGINS', '').strip()
        if origins_raw:
            return {
                'allow_origins': [origin.strip() for origin in origins_raw.split(',') if origin.strip()],
                'allow_origin_regex': None,
            }

        if cls.get_app_env() == 'production':
            return {
                'allow_origins': cls.DEFAULT_PRODUCTION_CORS_ORIGINS,
                'allow_origin_regex': None,
            }

        return {
            'allow_origins': [],
            'allow_origin_regex': cls.DEVELOPMENT_CORS_ORIGIN_REGEX,
        }

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
    COS_CI_IMAGE_AUDIT_ENABLED = os.environ.get('COS_CI_IMAGE_AUDIT_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    COS_CI_IMAGE_AUDIT_BIZ_TYPE = os.environ.get('COS_CI_IMAGE_AUDIT_BIZ_TYPE', '')
    COS_CI_IMAGE_AUDIT_TIMEOUT_SECONDS = _env_float('COS_CI_IMAGE_AUDIT_TIMEOUT_SECONDS', 8.0, minimum=1.0, maximum=30.0)
    COS_CI_IMAGE_AUDIT_LARGE_IMAGE_DETECT = os.environ.get('COS_CI_IMAGE_AUDIT_LARGE_IMAGE_DETECT', '0')

    # 阿里云移动推送配置 - 仅从环境变量读取，禁止在代码中写入真实密钥
    ALIYUN_ACCESS_KEY_ID = os.environ.get('ALIYUN_ACCESS_KEY_ID', '')
    ALIYUN_ACCESS_KEY_SECRET = os.environ.get('ALIYUN_ACCESS_KEY_SECRET', '')
    ALIYUN_PUSH_APP_KEY_ANDROID = os.environ.get('ALIYUN_PUSH_APP_KEY_ANDROID', '')
    ALIYUN_PUSH_ENDPOINT = os.environ.get('ALIYUN_PUSH_ENDPOINT', 'https://cloudpush.aliyuncs.com')
    ALIYUN_PUSH_REGION_ID = os.environ.get('ALIYUN_PUSH_REGION_ID', 'cn-hangzhou')
    ALIYUN_PUSH_ANDROID_ACTIVITY = os.environ.get('ALIYUN_PUSH_ANDROID_ACTIVITY', 'com.nonto.nonto.MainActivity')
    ALIYUN_PUSH_ANDROID_CHANNEL_ID = os.environ.get('ALIYUN_PUSH_ANDROID_CHANNEL_ID', 'nonto_message_alerts')

    # 邮件 (SMTP) 配置 - 用于发送邮箱验证码
    # QQ 邮箱用 SSL 465 端口，授权码非登录密码
    SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.qq.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')  # 授权码
    SMTP_FROM = os.environ.get('SMTP_FROM', '')
    SMTP_USE_SSL = os.environ.get('SMTP_USE_SSL', 'true').lower() == 'true'

    # 邮箱验证码限流配置
    OTP_RATE_LIMIT_PER_EMAIL_60S = 1      # 同邮箱 60s 内最多发 1 次
    OTP_RATE_LIMIT_PER_EMAIL_1H = 5       # 同邮箱 1 小时内最多 5 次
    OTP_RATE_LIMIT_PER_IP_1H = 10         # 同 IP 1 小时内最多 10 次
    OTP_EXPIRE_MINUTES = 10               # 验证码有效期 10 分钟
    LOGIN_FAIL_THRESHOLD = 5              # 登录失败 N 次后要求邮箱验证码

    DEBUG = os.environ.get('DEBUG', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    TESTING = False
    CORS_HEADERS = 'Content-Type'
