"""
Socket 处理 - FastAPI 兼容层
提供在线用户状态查询，不依赖 Flask-SocketIO
"""
import threading

# 线程安全的在线用户集合
_online_users: set = set()
_lock = threading.Lock()


def is_user_online(user_id: int) -> bool:
    """检查用户是否在线"""
    with _lock:
        return user_id in _online_users


def get_online_users() -> list:
    """获取所有在线用户 ID 列表"""
    with _lock:
        return list(_online_users)


def set_user_online(user_id: int):
    """标记用户为在线"""
    with _lock:
        _online_users.add(user_id)


def set_user_offline(user_id: int):
    """标记用户为离线"""
    with _lock:
        _online_users.discard(user_id)