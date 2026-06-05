"""
WebSocket 连接测试脚本
用于测试和查看 WebSocket 连接的详细信息
"""
import socketio
import time
import sys
import websocket

# Flask 服务器地址
BASE_URL = "http://127.0.0.1:5000"

# 检查依赖
try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False
    print("\n⚠️  警告: websocket-client 未安装")
    print("   请运行: pip install websocket-client")
    print("   否则只能使用 polling 传输模式\n")

def login_and_get_token(email="abc@qq.com", password="12345678"):
    """登录并获取JWT token，如果用户不存在则自动注册"""
    import requests
    
    # 先尝试登录
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": email,
        "password": password
    })
    
    if response.status_code == 200:
        data = response.json()
        return data.get('access_token')
    
    # 如果登录失败，尝试注册用户
    if response.status_code == 401:
        print(f"   用户不存在，尝试注册: {email}")
        
        # 生成唯一的用户名
        import time
        username = f"test_user_{int(time.time())}"
        
        register_response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "username": username,
            "email": email,
            "password": password,
            "first_name": "Test",
            "last_name": "User"
        })
        
        if register_response.status_code in [201, 409]:  # 201=成功, 409=已存在
            if register_response.status_code in [200, 201]:
                print(f"   ✅ 用户注册成功: {username}")
            else:
                print(f"   ⚠️  用户已存在")
            
            # 再次尝试登录
            login_response = requests.post(f"{BASE_URL}/api/auth/login", json={
                "email": email,
                "password": password
            })
            
            if login_response.status_code == 200:
                data = login_response.json()
                return data.get('access_token')
            else:
                print(f"   ❌ 注册后登录失败: {login_response.text}")
                return None
        else:
            print(f"   ❌ 注册失败: {register_response.text}")
            return None
    else:
        print(f"   ❌ 登录失败: {response.status_code}")
        print(f"   {response.text}")
        return None


def test_websocket_connection(token, user_name="User1"):
    """测试 WebSocket 连接"""
    print(f"\n{'='*60}")
    print(f"🧪 开始测试 {user_name} 的 WebSocket 连接")
    print('='*60)
    
    # 创建 Socket.IO 客户端
    sio = socketio.Client()
    
    @sio.event
    def connect():
        print(f"✅ {user_name}: WebSocket 连接成功!")
    
    @sio.event
    def connected(data):
        print(f"📩 {user_name}: 收到连接确认: {data}")
    
    @sio.event
    def disconnect():
        print(f"❌ {user_name}: WebSocket 已断开")
    
    @sio.on('error')
    def on_error(data):
        print(f"⚠️  {user_name}: 收到错误: {data}")
    
    @sio.on('user_status')
    def on_user_status(data):
        print(f"👤 {user_name}: 用户状态更新: {data}")
    
    @sio.event
    def connect_error(data):
        print(f"❌ {user_name}: 连接错误详情:")
        print(f"   错误数据: {data}")
        if hasattr(data, 'args'):
            print(f"   错误参数: {data.args}")
    
    try:
        # 连接到服务器，带上 token
        print(f"   正在连接到 {BASE_URL}...")
        
        # Socket.IO Python客户端使用headers或auth传递token
        # 但我们的后端期望在URL query参数中
        # 所以我们需要构造完整的URL
        ws_url = f"{BASE_URL}?token={token}"
        
        if HAS_WEBSOCKET:
            print(f"   传输模式: websocket (推荐)")
            sio.connect(ws_url, wait_timeout=10, transports=['websocket', 'polling'])
        else:
            print(f"   传输模式: polling (降级模式)")
            sio.connect(ws_url, wait_timeout=10, transports=['polling'])
        
        # 保持连接一段时间
        print(f"\n⏳ {user_name} 保持连接 10 秒...")
        time.sleep(10)
        
        # 断开连接
        sio.disconnect()
        print(f"✅ {user_name}: 测试完成\n")
        
    except Exception as e:
        print(f"❌ {user_name}: 连接错误: {e}")
        print(f"   错误类型: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        print()


def main():
    print("WebSocket 连接测试")
    print("="*60)
    
    # 获取测试用户的 token
    print("\n正在获取测试用户 token...")
    
    # 你可以修改这里的邮箱和密码
    users = [
        ("abc@qq.com", "12345678", "测试用户1"),
        # 如果有第二个测试账号，可以取消注释
        # ("test2@qq.com", "12345678", "测试用户2"),
    ]
    
    tokens = []
    for email, password, name in users:
        print(f"\n📝 处理 {name} ({email})...")
        token = login_and_get_token(email, password)
        if token:
            tokens.append((token, name, email))
            print(f"✅ {name} 登录成功")
        else:
            print(f"❌ {name} 登录失败")
    
    if not tokens:
        print("\n❌ 没有可用的 token，测试中止")
        return
    
    print(f"\n✅ 成功获取 {len(tokens)} 个 token")
    
    # 测试连接
    print("\n开始 WebSocket 连接测试...")
    print("请在后端控制台查看详细连接信息\n")
    
    for token, name, email in tokens:
        test_websocket_connection(token, name)
        # 每个测试之间间隔 2 秒
        if len(tokens) > 1:
            time.sleep(2)
    
    print("\n" + "="*60)
    print("✅ 所有测试完成!")
    print("="*60)
    print("\n提示:")
    print("- 查看后端控制台输出的详细连接信息")
    print("- 包括用户ID、会话ID、IP地址、User-Agent等")
    print("- 连接和断开时都会有清晰的格式化输出")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断测试")
        sys.exit(0)
