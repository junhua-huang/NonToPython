"""
测试邮箱搜索功能
"""
import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def login_and_get_token(username="testuser", password="testpass123"):
    """登录并获取JWT token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": username,
        "password": password
    })
    
    if response.status_code == 200:
        data = response.json()
        return data.get('access_token')
    else:
        print(f"登录失败: {response.status_code}")
        print(response.text)
        return None

def test_email_search(token, search_query, exclude_self=True):
    """测试邮箱搜索"""
    print(f"\n{'='*60}")
    print(f"测试搜索: '{search_query}' (exclude_self={exclude_self})")
    print('='*60)
    
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{BASE_URL}/api/search/global",
        params={
            "q": search_query, 
            "page": 1, 
            "per_page": 20,
            "exclude_self": str(exclude_self).lower()
        },
        headers=headers
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n搜索结果:")
        print(f"  用户数量: {data['user_total']}")
        print(f"  帖子数量: {data['post_total']}")
        print(f"  总结果数: {data['total_results']}")
        
        if data['users']:
            print(f"\n匹配的用户:")
            for user in data['users']:
                print(f"  - ID: {user['id']}")
                print(f"    用户名: {user['username']}")
                print(f"    邮箱: {user['email']}")
                print(f"    姓名: {user['full_name']}")
        
        if data['posts']:
            print(f"\n匹配的帖子: {len(data['posts'])} 条")
        
        return data
    else:
        print(f"错误: {response.text}")
        return None

def main():
    print("开始测试邮箱搜索功能...")
    
    # 登录
    token = login_and_get_token()
    if not token:
        print("无法获取token,测试中止")
        return
    
    print("✓ 登录成功")
    
    # 测试不同的搜索模式
    test_cases = [
        ("abc@qq.com", True),   # 完整邮箱，排除自己
        ("abc@qq.com", False),  # 完整邮箱，包含自己
        ("abc", True),          # 仅用户名部分，排除自己
        ("abc", False),         # 仅用户名部分，包含自己
    ]
    
    results = {}
    for query, exclude_self in test_cases:
        data = test_email_search(token, query, exclude_self)
        results[f"'{query}' (exclude={exclude_self})"] = data
    
    # 总结
    print(f"\n{'='*60}")
    print("测试总结")
    print('='*60)
    for query, data in results.items():
        if data:
            print(f"'{query}' -> 用户: {data['user_total']}, 帖子: {data['post_total']}")
        else:
            print(f"'{query}' -> 请求失败")

if __name__ == "__main__":
    main()
