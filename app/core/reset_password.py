"""
重置指定用户的密码 - FastAPI 版本
用法: python reset_password.py <email> <new_password>
"""
import sys
import bcrypt

from app.database import SessionLocal
from app.models.models import User


def reset_password(email, new_password):
    """重置用户密码"""
    db = SessionLocal()
    try:
        # 查找用户
        user = db.query(User).filter(User.email == email).first()

        if not user:
            print(f"❌ 错误: 用户 {email} 不存在")
            return False

        # 生成新的密码哈希
        new_password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # 更新密码
        user.password_hash = new_password_hash
        db.commit()

        print(f"✅ 成功重置用户 {email} 的密码")
        print(f"   新密码: {new_password}")
        print(f"   密码哈希: {new_password_hash[:50]}...")

        # 验证新密码
        is_valid = bcrypt.checkpw(new_password.encode('utf-8'), user.password_hash.encode('utf-8'))
        print(f"   验证结果: {'✅ 通过' if is_valid else '❌ 失败'}")

        return True
    finally:
        db.close()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("用法: python reset_password.py <email> <new_password>")
        print("示例: python reset_password.py abc@qq.com 12345678")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    reset_password(email, password)
