"""
搜索API测试脚本 - 验证类型转换和查询修复
"""
import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def login_and_get_token():
    """登录并获取JWT token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "testuser",
        "password": "testpass123"
    })
    
    if response.status_code == 200:
        data = response.json()
        return data.get('access_token')
    else:
        print(f"登录失败: {response.status_code}")
        print(response.text)
        return None

def test_global_search(token):
    """测试全局搜索 - 验证返回类型"""
    print("\n=== 测试全局搜索 ===")
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{BASE_URL}/api/search/global",
        params={"q": "abc", "page": 1, "per_page": 20},
        headers=headers
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"响应数据: {json.dumps(data, indent=2)}")
        
        # 验证类型
        assert isinstance(data['current_page'], int), f"current_page 应该是 int, 实际是 {type(data['current_page'])}"
        assert isinstance(data['per_page'], int), f"per_page 应该是 int, 实际是 {type(data['per_page'])}"
        assert isinstance(data['post_total'], int), f"post_total 应该是 int, 实际是 {type(data['post_total'])}"
        assert isinstance(data['total_results'], int), f"total_results 应该是 int, 实际是 {type(data['total_results'])}"
        assert isinstance(data['user_total'], int), f"user_total 应该是 int, 实际是 {type(data['user_total'])}"
        
        print("✓ 所有数字字段类型正确 (int)")
    else:
        print(f"错误: {response.text}")

def test_save_search_history(token):
    """测试保存搜索历史"""
    print("\n=== 测试保存搜索历史 ===")
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.post(
        f"{BASE_URL}/api/search/history",
        json={"query": "abc", "type": "global"},
        headers=headers
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code in [201, 200]:
        data = response.json()
        print(f"响应: {json.dumps(data, indent=2)}")
        print("✓ 搜索历史保存成功")
    else:
        print(f"错误: {response.text}")
        raise Exception("保存搜索历史失败")

def test_get_search_history(token):
    """测试获取搜索历史"""
    print("\n=== 测试获取搜索历史 ===")
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{BASE_URL}/api/search/history",
        headers=headers
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"历史记录数量: {data.get('total', 0)}")
        print(f"响应: {json.dumps(data, indent=2)}")
        print("✓ 获取搜索历史成功")
    else:
        print(f"错误: {response.text}")
        raise Exception("获取搜索历史失败")

def main():
    print("开始测试搜索API修复...")
    
    # 登录
    token = login_and_get_token()
    if not token:
        print("无法获取token,测试中止")
        return
    
    print("✓ 登录成功")
    
    try:
        # 测试全局搜索
        test_global_search(token)
        
        # 测试保存搜索历史
        test_save_search_history(token)
        
        # 测试获取搜索历史
        test_get_search_history(token)
        
        print("\n" + "="*50)
        print("✓ 所有测试通过!")
        print("="*50)
        
    except Exception as e:
        print(f"\n✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
