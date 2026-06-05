"""
搜索接口测试脚本
测试所有搜索相关API的功能
"""
import requests
import json
import time

# 配置
BASE_URL = "http://localhost:5000/api"
HEADERS = {
    "Content-Type": "application/json"
}

# 测试结果统计
test_results = {
    "passed": 0,
    "failed": 0,
    "total": 0
}


def print_test(name):
    """打印测试标题"""
    print(f"\n{'='*60}")
    print(f"🧪 测试: {name}")
    print('='*60)


def print_success(message):
    """打印成功信息"""
    print(f"✅ {message}")
    test_results["passed"] += 1
    test_results["total"] += 1


def print_error(message, response=None):
    """打印错误信息"""
    print(f"❌ {message}")
    if response is not None:
        print(f"   状态码: {response.status_code}")
        try:
            print(f"   响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
        except:
            print(f"   响应: {response.text[:200]}")
    test_results["failed"] += 1
    test_results["total"] += 1


def print_skip(message):
    """打印跳过信息"""
    print(f"⏭️  {message}")
    test_results["total"] += 1


def register_user(username, email, password):
    """注册用户"""
    url = f"{BASE_URL}/auth/register"
    data = {
        "username": username,
        "email": email,
        "password": password,
        "first_name": username,
        "bio": "测试用户"
    }
    
    try:
        response = requests.post(url, json=data, headers=HEADERS)
        if response.status_code in [200, 201]:
            return True, response.json()
        elif response.status_code == 409:
            # 用户已存在
            return True, {"message": "User already exists"}
        else:
            return False, response.json()
    except Exception as e:
        return False, str(e)


def login(email, password):
    """用户登录"""
    url = f"{BASE_URL}/auth/login"
    data = {
        "email": email,
        "password": password
    }
    
    try:
        response = requests.post(url, json=data, headers=HEADERS)
        if response.status_code == 200:
            return True, response.json()
        else:
            return False, response.json()
    except Exception as e:
        return False, str(e)


def create_post(token, content):
    """创建帖子"""
    url = f"{BASE_URL}/posts"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    data = {
        "content": content,
        "visibility": "public"
    }
    
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code in [200, 201]:
            return True, response.json()
        else:
            return False, response.json()
    except Exception as e:
        return False, str(e)


def test_search_users(token):
    """测试搜索用户"""
    print_test("搜索用户")
    
    url = f"{BASE_URL}/search/users"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    # 测试1: 搜索存在的用户
    params = {"q": "test", "page": 1, "per_page": 10}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        user_count = len(data.get('users', []))
        print_success(f"搜索用户成功，找到 {user_count} 个用户")
        print(f"   总数: {data.get('total', 0)}, 页数: {data.get('pages', 0)}")
        
        if user_count > 0:
            print(f"   第一个用户: {data['users'][0].get('username', 'N/A')}")
    else:
        print_error("搜索用户失败", response)
    
    # 测试2: 空查询
    params = {"q": "", "page": 1}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 400:
        print_success("空查询正确返回400错误")
    else:
        print_error("空查询应该返回400错误", response)


def test_search_posts(token):
    """测试搜索帖子"""
    print_test("搜索帖子")
    
    url = f"{BASE_URL}/search/posts"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    # 测试1: 搜索包含特定关键词的帖子
    params = {"q": "测试", "page": 1, "per_page": 10}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        post_count = len(data.get('posts', []))
        print_success(f"搜索帖子成功，找到 {post_count} 个帖子")
        print(f"   总数: {data.get('total', 0)}, 页数: {data.get('pages', 0)}")
        
        if post_count > 0:
            first_post = data['posts'][0]
            print(f"   第一个帖子: {first_post.get('content', 'N/A')[:50]}...")
    else:
        print_error("搜索帖子失败", response)
    
    # 测试2: 按用户ID搜索
    params = {"q": "测试", "user_id": 1, "page": 1}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        print_success("按用户ID搜索帖子成功")
    else:
        print_error("按用户ID搜索帖子失败", response)


def test_global_search(token):
    """测试全局综合搜索"""
    print_test("全局综合搜索")
    
    url = f"{BASE_URL}/search/global"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    params = {"q": "test", "page": 1, "per_page": 5}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        user_count = len(data.get('users', []))
        post_count = len(data.get('posts', []))
        total = data.get('total_results', 0)
        
        print_success(f"全局搜索成功")
        print(f"   用户: {user_count} 个, 帖子: {post_count} 个, 总计: {total}")
        print(f"   查询关键词: {data.get('query', 'N/A')}")
    else:
        print_error("全局搜索失败", response)


def test_hashtag_search(token):
    """测试标签搜索"""
    print_test("按标签搜索帖子")
    
    url = f"{BASE_URL}/search/hashtag/life"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    params = {"page": 1, "per_page": 10}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        post_count = len(data.get('posts', []))
        print_success(f"标签搜索成功，找到 {post_count} 个包含 #life 的帖子")
        print(f"   标签: {data.get('hashtag', 'N/A')}")
    else:
        print_error("标签搜索失败", response)


def test_trending_hashtags(token):
    """测试获取热门标签"""
    print_test("获取热门标签")
    
    url = f"{BASE_URL}/search/trending-hashtags"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    params = {"limit": 10}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        hashtags = data.get('hashtags', [])
        print_success(f"获取热门标签成功，共 {len(hashtags)} 个")
        
        if hashtags:
            print("   热门话题:")
            for i, tag in enumerate(hashtags[:5], 1):
                print(f"     {i}. #{tag['hashtag']} ({tag['count']} 次)")
    else:
        print_error("获取热门标签失败", response)


def test_user_suggestions(token):
    """测试用户自动补全建议"""
    print_test("用户自动补全建议")
    
    url = f"{BASE_URL}/search/suggest/users"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    # 测试1: 有前缀
    params = {"prefix": "t", "limit": 5}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        suggestions = data.get('suggestions', [])
        print_success(f"用户建议成功，返回 {len(suggestions)} 个建议")
        
        if suggestions:
            print("   建议用户:")
            for user in suggestions[:3]:
                print(f"     - {user.get('username', 'N/A')} ({user.get('full_name', 'N/A')})")
    else:
        print_error("用户建议失败", response)
    
    # 测试2: 空前缀
    params = {"prefix": "", "limit": 5}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if len(data.get('suggestions', [])) == 0:
            print_success("空前缀正确返回空列表")
    else:
        print_error("空前缀测试失败", response)


def test_mention_suggestions(token):
    """测试@提及用户建议"""
    print_test("@提及用户建议")
    
    url = f"{BASE_URL}/search/mention-suggestions"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    params = {"prefix": "t", "limit": 5}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        suggestions = data.get('suggestions', [])
        print_success(f"@提及建议成功，返回 {len(suggestions)} 个建议")
        
        if suggestions:
            print("   建议用户:")
            for user in suggestions[:3]:
                friend_tag = " [好友]" if user.get('is_friend') else ""
                print(f"     - {user.get('username', 'N/A')}{friend_tag}")
    else:
        print_error("@提及建议失败", response)


def test_search_history(token):
    """测试搜索历史"""
    print_test("搜索历史管理")
    
    url = f"{BASE_URL}/search/history"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    # 测试1: 保存搜索历史
    save_data = {
        "query": "测试搜索",
        "type": "global"
    }
    response = requests.post(url, json=save_data, headers=headers)
    
    if response.status_code in [200, 201]:
        print_success("保存搜索历史成功")
    else:
        print_error("保存搜索历史失败", response)
    
    # 测试2: 获取搜索历史
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print_success("获取搜索历史成功")
    else:
        print_error("获取搜索历史失败", response)
    
    # 测试3: 清空搜索历史
    response = requests.delete(url, headers=headers)
    
    if response.status_code == 200:
        print_success("清空搜索历史成功")
    else:
        print_error("清空搜索历史失败", response)


def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("🔍 开始测试搜索接口")
    print("="*60)
    
    # Step 1: 准备测试数据
    print("\n📝 准备测试数据...")
    
    # 注册/登录测试用户1
    success1, result1 = register_user("testuser1", "test1@search.com", "password123")
    if not success1:
        print(f"❌ 注册/登录用户1失败: {result1}")
        return
    
    success1, login_result1 = login("test1@search.com", "password123")
    if not success1:
        print(f"❌ 登录用户1失败: {login_result1}")
        return
    
    token1 = login_result1['access_token']
    print(f"✅ 用户1登录成功: {login_result1['user']['username']}")
    
    # 注册/登录测试用户2
    success2, result2 = register_user("testuser2", "test2@search.com", "password123")
    if not success2:
        print(f"❌ 注册/登录用户2失败: {result2}")
        return
    
    success2, login_result2 = login("test2@search.com", "password123")
    if not success2:
        print(f"❌ 登录用户2失败: {login_result2}")
        return
    
    token2 = login_result2['access_token']
    print(f"✅ 用户2登录成功: {login_result2['user']['username']}")
    
    # 创建一些测试帖子
    print("\n📝 创建测试帖子...")
    test_posts = [
        "这是一个测试帖子 #life #test",
        "今天天气真好，适合出去走走 #life",
        "分享一些编程技巧 #coding #tech",
        "美食推荐：今天的晚餐很棒 #food #life",
        "测试搜索功能的帖子"
    ]
    
    for content in test_posts:
        success, result = create_post(token1, content)
        if success:
            print(f"✅ 创建帖子: {content[:30]}...")
        else:
            print(f"⚠️  创建帖子失败: {result}")
    
    time.sleep(1)  # 等待数据库更新
    
    # Step 2: 执行搜索测试
    print("\n" + "="*60)
    print("🚀 开始执行搜索测试")
    print("="*60)
    
    test_search_users(token1)
    test_search_posts(token1)
    test_global_search(token1)
    test_hashtag_search(token1)
    test_trending_hashtags(token1)
    test_user_suggestions(token1)
    test_mention_suggestions(token1)
    test_search_history(token1)
    
    # Step 3: 输出测试结果
    print("\n" + "="*60)
    print("📊 测试结果汇总")
    print("="*60)
    print(f"总测试数: {test_results['total']}")
    print(f"✅ 通过: {test_results['passed']}")
    print(f"❌ 失败: {test_results['failed']}")
    
    if test_results['failed'] == 0:
        print("\n🎉 所有测试通过！")
    else:
        print(f"\n⚠️  有 {test_results['failed']} 个测试失败，请检查上面的错误信息")
    
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
