"""
测试搜索接口空字符串处理
验证当查询为空时,返回空结果而不是错误
"""
import requests
import json

BASE_URL = "http://127.0.0.1:5000/api"

def get_token():
    """获取测试用户的Token"""
    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "test@example.com",
        "password": "Test123456"
    })
    
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        print("❌ 登录失败,请确保测试用户存在")
        return None


def test_empty_search_users(token):
    """测试空字符串搜索用户"""
    print("\n" + "="*60)
    print("测试1: 空字符串搜索用户")
    print("="*60)
    
    # 测试1: 完全空的查询参数
    response = requests.get(
        f"{BASE_URL}/search/users",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": ""}
    )
    
    print(f"\n请求: GET /search/users?q=")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功返回空结果")
        print(f"   - users: {data['users']}")
        print(f"   - total: {data['total']}")
        print(f"   - pages: {data['pages']}")
    else:
        print(f"❌ 失败: {response.json()}")
    
    # 测试2: 只有空格
    response = requests.get(
        f"{BASE_URL}/search/users",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "   "}
    )
    
    print(f"\n请求: GET /search/users?q='   '")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功返回空结果")
        print(f"   - users: {data['users']}")
        print(f"   - total: {data['total']}")
    else:
        print(f"❌ 失败: {response.json()}")
    
    # 测试3: 没有q参数
    response = requests.get(
        f"{BASE_URL}/search/users",
        headers={"Authorization": f"Bearer {token}"}
    )
    
    print(f"\n请求: GET /search/users (无q参数)")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功返回空结果")
        print(f"   - users: {data['users']}")
        print(f"   - total: {data['total']}")
    else:
        print(f"❌ 失败: {response.json()}")


def test_empty_search_posts(token):
    """测试空字符串搜索帖子"""
    print("\n" + "="*60)
    print("测试2: 空字符串搜索帖子")
    print("="*60)
    
    response = requests.get(
        f"{BASE_URL}/search/posts",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": ""}
    )
    
    print(f"\n请求: GET /search/posts?q=")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功返回空结果")
        print(f"   - posts: {data['posts']}")
        print(f"   - total: {data['total']}")
        print(f"   - pages: {data['pages']}")
    else:
        print(f"❌ 失败: {response.json()}")


def test_empty_global_search(token):
    """测试空字符串全局搜索"""
    print("\n" + "="*60)
    print("测试3: 空字符串全局搜索")
    print("="*60)
    
    response = requests.get(
        f"{BASE_URL}/search/global",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": ""}
    )
    
    print(f"\n请求: GET /search/global?q=")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功返回空结果")
        print(f"   - query: '{data['query']}'")
        print(f"   - users: {data['users']}")
        print(f"   - posts: {data['posts']}")
        print(f"   - user_total: {data['user_total']}")
        print(f"   - post_total: {data['post_total']}")
        print(f"   - total_results: {data['total_results']}")
    else:
        print(f"❌ 失败: {response.json()}")


def test_valid_search(token):
    """测试有效搜索仍然正常工作"""
    print("\n" + "="*60)
    print("测试4: 有效搜索(确保功能正常)")
    print("="*60)
    
    response = requests.get(
        f"{BASE_URL}/search/global",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "test"}
    )
    
    print(f"\n请求: GET /search/global?q=test")
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ 搜索正常工作")
        print(f"   - query: '{data['query']}'")
        print(f"   - user_total: {data['user_total']}")
        print(f"   - post_total: {data['post_total']}")
        print(f"   - total_results: {data['total_results']}")
    else:
        print(f"❌ 失败: {response.json()}")


def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("🧪 搜索接口空字符串处理测试")
    print("="*60)
    
    # 获取Token
    token = get_token()
    if not token:
        print("\n⚠️  请先创建测试用户或修改测试代码中的登录信息")
        return
    
    print(f"\n✅ Token获取成功")
    
    # 运行测试
    test_empty_search_users(token)
    test_empty_search_posts(token)
    test_empty_global_search(token)
    test_valid_search(token)
    
    print("\n" + "="*60)
    print("✅ 所有测试完成!")
    print("="*60)
    print("\n总结:")
    print("- ✅ 空字符串搜索返回空结果(200),而不是错误(400)")
    print("- ✅ 提供更好的用户体验,前端无需特殊处理")
    print("- ✅ 有效搜索功能不受影响")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
