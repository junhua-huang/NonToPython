"""
pytest 测试配置文件 - FastAPI 版本
提供 token, app, client, db, image_url, search_query 等 fixture
"""
import pytest
import threading
import time
import requests
import sys
import os
from io import BytesIO
from fastapi.testclient import TestClient

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.database import SessionLocal

BASE_URL = "http://127.0.0.1:8898"


@pytest.fixture(scope='session')
def fastapi_app():
    """FastAPI 应用实例（session 级别）"""
    return app


@pytest.fixture(scope='session')
def live_server():
    """在后台线程启动 uvicorn 服务器，供 requests-based 测试使用"""
    import uvicorn

    def _run():
        uvicorn.run(
            "app.main:app",
            host='127.0.0.1',
            port=8898,
            reload=False,
            log_level='error',
        )

    server_thread = threading.Thread(target=_run, daemon=True)
    server_thread.start()

    # 等待服务器就绪，最多重试 10 次
    for i in range(10):
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                break
        except requests.ConnectionError:
            pass
        time.sleep(1)
    else:
        pytest.fail("uvicorn 服务器在 10 秒内未能启动")

    yield
    # daemon 线程随主进程退出


@pytest.fixture(scope='session')
def token(live_server):
    """
    注册测试用户并登录，返回 JWT access token。
    """
    import random
    timestamp = random.randint(10000, 99999)
    email = f"pytest_{timestamp}@test.com"
    password = "TestPass123"
    username = f"pytest_user_{timestamp}"

    # 步骤1: 注册新用户
    reg_resp = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "first_name": "Pytest",
            "last_name": "User",
        },
        timeout=10,
    )

    # 步骤2: 登录获取 token
    login_resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )

    if login_resp.status_code == 200:
        return login_resp.json()["access_token"]

    # 回退：尝试已有测试用户
    for fallback_email, fallback_pass in [
        ("test@example.com", "Test123456"),
        ("test1@search.com", "password123"),
        ("testuser", "testpass123"),
    ]:
        payload = {"email": fallback_email, "password": fallback_pass}
        resp = requests.post(f"{BASE_URL}/api/auth/login", json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()["access_token"]

    pytest.skip("无法获取认证 token - 服务器可能未运行或测试用户不存在")


@pytest.fixture(scope='session')
def client(fastapi_app):
    """FastAPI TestClient"""
    with TestClient(fastapi_app) as _client:
        yield _client


@pytest.fixture(scope='session')
def db():
    """数据库 session fixture"""
    db_session = SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()


@pytest.fixture(scope='session')
def image_url(token):
    """上传一张测试图片并返回其 URL"""
    try:
        from PIL import Image
    except ImportError:
        return None

    img = Image.new('RGB', (100, 100), color=(73, 109, 137))
    img_bytes = BytesIO()
    img.save(img_bytes, format='JPEG')
    img_bytes.seek(0)

    resp = requests.post(
        f"{BASE_URL}/api/upload/post/image",
        headers={"Authorization": f"Bearer {token}"},
        files={'file': ('test_image.jpg', img_bytes, 'image/jpeg')},
        timeout=10,
    )

    if resp.status_code in [200, 201]:
        return resp.json()['url']
    return None


@pytest.fixture(scope='session')
def search_query():
    """默认搜索查询关键词"""
    return "test"