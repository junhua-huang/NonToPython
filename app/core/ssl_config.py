import os

class SSLConfig:
    """SSL/TLS 配置"""
    # 证书文件路径 (Let's Encrypt 标准路径)
    CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ssl')
    CERT_FILE = os.path.join(CERT_DIR, 'cert.pem')
    KEY_FILE = os.path.join(CERT_DIR, 'key.pem')
    
    # 是否启用 SSL
    SSL_ENABLED = os.environ.get('SSL_ENABLED', 'false').lower() == 'true'
    
    # HTTP 重定向到 HTTPS
    FORCE_HTTPS = os.environ.get('FORCE_HTTPS', 'false').lower() == 'true'
    
    @classmethod
    def ensure_cert_dir(cls):
        """确保证书目录存在"""
        if not os.path.exists(cls.CERT_DIR):
            os.makedirs(cls.CERT_DIR)
    
    @classmethod
    def get_ssl_context(cls):
        """获取 SSL 上下文 (用于开发环境自签名证书)"""
        cls.ensure_cert_dir()
        
        # 生产环境使用真实证书
        if os.path.exists(cls.CERT_FILE) and os.path.exists(cls.KEY_FILE):
            return (cls.CERT_FILE, cls.KEY_FILE)
        
        return None
