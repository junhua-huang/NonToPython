"""
WebSocket 连接诊断工具
用于检查 WebSocket 服务器状态和连接问题
"""
import socket
import requests
import sys

SERVER_URL = "http://10.0.2.2:5000"
WS_URL = "ws://10.0.2.2:5000"

def check_server_running():
    """检查服务器是否运行"""
    print("\n" + "="*60)
    print("🔍 步骤 1: 检查服务器是否运行")
    print("="*60)
    
    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ 服务器正在运行")
            print(f"   响应: {response.json()}")
            return True
        else:
            print(f"❌ 服务器返回错误状态码: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器")
        print("   可能原因:")
        print("   1. Flask服务器未启动")
        print("   2. 服务器监听的地址/端口不正确")
        print("   3. 防火墙阻止了连接")
        return False
    except requests.exceptions.Timeout:
        print("❌ 连接超时")
        return False


def check_port_listening():
    """检查端口是否在监听"""
    print("\n" + "="*60)
    print("🔍 步骤 2: 检查端口 5000 是否监听")
    print("="*60)
    
    # 尝试连接到服务器
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    
    try:
        result = sock.connect_ex(('10.0.2.2', 5000))
        if result == 0:
            print("✅ 端口 5000 正在监听")
            return True
        else:
            print(f"❌ 端口 5000 未监听 (错误码: {result})")
            return False
    except Exception as e:
        print(f"❌ 检查端口失败: {e}")
        return False
    finally:
        sock.close()


def check_websocket_endpoint():
    """检查WebSocket端点是否可访问"""
    print("\n" + "="*60)
    print("🔍 步骤 3: 检查 WebSocket 端点")
    print("="*60)
    
    try:
        # Socket.IO 会先进行 HTTP 握手
        response = requests.get(
            f"{SERVER_URL}/socket.io/",
            params={"EIO": "4", "transport": "polling"},
            timeout=5
        )
        
        if response.status_code == 200:
            print("✅ WebSocket 端点可访问")
            print(f"   响应前100字符: {response.text[:100]}")
            return True
        else:
            print(f"❌ WebSocket 端点返回错误: {response.status_code}")
            print(f"   响应: {response.text[:200]}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到 WebSocket 端点")
        return False
    except requests.exceptions.Timeout:
        print("❌ WebSocket 端点连接超时")
        return False


def test_socketio_connection():
    """测试 Socket.IO 连接"""
    print("\n" + "="*60)
    print("🔍 步骤 4: 测试 Socket.IO 连接")
    print("="*60)
    
    try:
        import socketio
        
        sio = socketio.Client()
        connected = False
        error_message = None
        
        @sio.event
        def connect():
            nonlocal connected
            connected = True
            print("✅ Socket.IO 连接成功!")
        
        @sio.event
        def connect_error(data):
            nonlocal error_message
            error_message = data
            print(f"❌ Socket.IO 连接错误: {data}")
        
        @sio.event
        def disconnect():
            print("⚠️  Socket.IO 连接断开")
        
        # 尝试连接 (不带token,应该会收到认证错误)
        print("   尝试连接到服务器...")
        sio.connect(SERVER_URL, wait_timeout=5)
        
        if connected:
            print("✅ 基本连接成功 (未认证)")
            sio.disconnect()
            return True
        else:
            print(f"❌ 连接失败: {error_message}")
            return False
            
    except ImportError:
        print("⚠️  python-socketio 未安装,跳过此测试")
        print("   安装命令: pip install python-socketio[client]")
        return None
    except Exception as e:
        print(f"❌ Socket.IO 连接异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def print_troubleshooting_tips():
    """打印故障排除建议"""
    print("\n" + "="*60)
    print("💡 故障排除建议")
    print("="*60)
    print("""
1. 确保 Flask 服务器正在运行:
   - 检查是否有 "Running on http://0.0.0.0:5000" 的输出
   - 如果没有,运行: python app.py

2. 检查服务器绑定地址:
   - 当前配置: host='0.0.0.0' (正确)
   - 这允许从任何IP地址连接

3. 检查防火墙设置:
   - Windows防火墙可能阻止了5000端口
   - 临时关闭防火墙测试,或添加入站规则

4. Android模拟器网络:
   - 10.0.2.2 是Android模拟器访问宿主机的特殊地址
   - 确保使用的是Android模拟器,不是真机
   - 如果是真机,需要使用电脑的实际IP地址

5. 检查Token有效性:
   - Token可能已过期
   - 尝试重新登录获取新Token
   - Token格式: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

6. 查看后端日志:
   - 检查 app.log 文件
   - 查看是否有任何错误信息
   - 连接时应该有 "🔌 WebSocket 新连接" 的日志

7. 检查CORS配置:
   - 当前配置: cors_allowed_origins="*"
   - 应该允许所有来源

8. 重启服务:
   - 停止Flask服务器 (Ctrl+C)
   - 重新启动: python app.py
    """)


def main():
    print("\n" + "🔧"*30)
    print("  WebSocket 连接诊断工具")
    print("🔧"*30)
    
    results = []
    
    # 执行各项检查
    results.append(("服务器运行状态", check_server_running()))
    results.append(("端口监听状态", check_port_listening()))
    results.append(("WebSocket端点", check_websocket_endpoint()))
    results.append(("Socket.IO连接", test_socketio_connection()))
    
    # 总结
    print("\n" + "="*60)
    print("📊 诊断结果总结")
    print("="*60)
    
    passed = 0
    total = 0
    
    for name, result in results:
        total += 1
        if result is True:
            print(f"✅ {name}: 通过")
            passed += 1
        elif result is False:
            print(f"❌ {name}: 失败")
        else:
            print(f"⚠️  {name}: 跳过")
    
    print(f"\n总计: {passed}/{total} 项通过")
    
    if passed == total:
        print("\n✅ 所有检查通过! 问题可能在其他地方。")
    else:
        print("\n❌ 发现一些问题,请根据上述建议进行修复。")
    
    # 打印故障排除建议
    print_troubleshooting_tips()
    
    print("\n" + "="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  诊断中断")
        sys.exit(0)
