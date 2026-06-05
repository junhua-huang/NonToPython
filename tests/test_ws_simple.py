"""
简单的 WebSocket 连接测试 - 不带认证
用于诊断基本连接问题
"""
import socketio
import time
import logging

# 启用详细日志
logging.basicConfig(level=logging.DEBUG)

BASE_URL = "http://127.0.0.1:5000"

print("="*60)
print("简单 WebSocket 连接测试")
print("="*60)

sio = socketio.Client(logger=True, engineio_logger=True)

@sio.event
def connect():
    print("✅ 连接成功!")

@sio.event
def connected(data):
    print(f"📩 收到确认: {data}")

@sio.event
def disconnect():
    print("❌ 连接断开")

@sio.on('error')
def on_error(data):
    print(f"⚠️  收到错误: {data}")

@sio.event
def connect_error(data):
    print(f"❌ 连接错误:")
    print(f"   数据: {data}")

try:
    print(f"\n正在连接到 {BASE_URL} (不带token)...")
    print("提示: 查看上面的DEBUG日志了解详细信息\n")
    sio.connect(BASE_URL, wait_timeout=10, transports=['polling'])
    
    print("\n保持连接 5 秒...")
    time.sleep(5)
    
    sio.disconnect()
    print("\n✅ 测试完成")
    
except Exception as e:
    print(f"\n❌ 连接失败: {e}")
    print(f"   错误类型: {type(e).__name__}")
    import traceback
    traceback.print_exc()
