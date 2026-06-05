"""
诊断搜索问题 - FastAPI 版本
检查数据库中是否有匹配的用户
"""
from sqlalchemy import or_
from app.database import SessionLocal
from app.models.models import User


def diagnose_search(query: str):
    db = SessionLocal()
    try:
        search_term = f'%{query}%'

        print(f"搜索关键词: {query}")
        print(f"搜索模式: {search_term}")
        print("=" * 60)

        # 1. 检查所有用户
        all_users = db.query(User).all()
        print(f"\n数据库中共有 {len(all_users)} 个用户:")
        for user in all_users:
            print(f"  - ID: {user.id}, 用户名: {user.username}, 邮箱: {user.email}, 激活: {user.is_active}")

        # 2. 执行实际搜索查询
        print(f"\n执行搜索查询 (LIKE '{search_term}'):")
        user_query = db.query(User).filter(
            or_(
                User.username.ilike(search_term),
                User.first_name.ilike(search_term),
                User.last_name.ilike(search_term),
                User.email.ilike(search_term),
            ),
            User.is_active == True,
        )

        results = user_query.all()
        print(f"找到 {len(results)} 个匹配的用户:")
        for user in results:
            print(f"  - ID: {user.id}, 用户名: {user.username}, 邮箱: {user.email}")

        # 3. 测试不同的搜索模式
        print("\n" + "=" * 60)
        print("尝试不同的搜索模式:")

        test_patterns = [
            "abc",
            "abc@",
            "@qq.com",
            "qq.com",
            "abc@qq.com",
        ]

        for pattern in test_patterns:
            test_term = f'%{pattern}%'
            count = db.query(User).filter(
                or_(
                    User.username.ilike(test_term),
                    User.email.ilike(test_term),
                ),
                User.is_active == True,
            ).count()
            print(f"  '{pattern}' -> 找到 {count} 个用户")

        # 4. 检查是否有非激活用户
        inactive_users = db.query(User).filter(
            or_(
                User.email.ilike(search_term),
                User.username.ilike(search_term),
            ),
            User.is_active == False,
        ).all()

        if inactive_users:
            print(f"\n⚠️  发现 {len(inactive_users)} 个未激活的匹配用户:")
            for user in inactive_users:
                print(f"  - ID: {user.id}, 邮箱: {user.email}, 激活状态: {user.is_active}")
    finally:
        db.close()


if __name__ == '__main__':
    diagnose_search("abc@qq.com")
