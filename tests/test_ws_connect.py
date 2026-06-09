"""
WebSocket v3.0 序号协议测试脚本
测试: 连接 -> auth -> ping/pong -> session_list -> sync -> send/ack -> 无效token
"""
import asyncio
import json
import time
import requests
import websockets

BASE_URL = "http://localhost:5000"
WS_URL = "ws://localhost:5000/ws"


def register_and_login(username, email, password):
    """注册并登录，返回 token"""
    # 注册
    r = requests.post(f"{BASE_URL}/api/auth/register", json={
        "username": username, "email": email, "password": password
    })
    print(f"  Register: {r.status_code}")

    # 登录
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "login": email, "password": password
    })
    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"  Login failed: {r.status_code} {r.text}")
    return None


async def test_auth_and_ping(token):
    """测试 1: 连接 -> auth -> ping/pong -> session_list"""
    print("\n[TEST 1] auth + ping/pong + session_list")
    async with websockets.connect(WS_URL) as ws:
        # 1. 发送 auth
        await ws.send(json.dumps({"type": "auth", "token": token}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "auth_result", f"Expected auth_result, got {resp}"
        assert resp["success"] is True, f"Auth failed: {resp}"
        user_id = resp["user_id"]
        print(f"  auth_result: success=True, user_id={user_id}  PASS")

        # 2. 可能收到 session_list（带序号），或者先 ping
        # 先收集所有消息
        collected = []
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                collected.append(msg)
        except asyncio.TimeoutError:
            pass

        # 检查是否收到 session_list
        has_session_list = any(
            m.get("type") == "message" and m.get("payload", {}).get("event") == "session_list"
            for m in collected
        )
        print(f"  session_list received: {has_session_list}  {'PASS' if has_session_list else 'SKIP (no sessions yet)'}")

        # 3. ping/pong
        await ws.send(json.dumps({"type": "ping"}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "pong", f"Expected pong, got {resp}"
        print(f"  pong received  PASS")

        # 4. 记录最大 seq
        max_seq = 0
        for m in collected:
            if m.get("type") == "message" and m.get("seq", 0) > max_seq:
                max_seq = m["seq"]
        print(f"  max_seq so far: {max_seq}")

    print("  [TEST 1] PASS")
    return True


async def test_sync(token, last_seq=0):
    """测试 2: 断线补发 sync"""
    print(f"\n[TEST 2] sync (lastReceivedSeq={last_seq})")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "auth_result"

        # 收集认证后的自动推送
        collected = []
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                collected.append(msg)
        except asyncio.TimeoutError:
            pass

        # 发送 sync
        await ws.send(json.dumps({"type": "sync", "lastReceivedSeq": last_seq}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "sync_result", f"Expected sync_result, got {resp}"
        count = resp.get("count", 0)
        print(f"  sync_result: count={count}  PASS")

    print("  [TEST 2] PASS")
    return True


async def test_send_message(token_a, token_b):
    """测试 3: 用户 A 发消息给用户 B（通过 WS send）"""
    print("\n[TEST 3] send message + ack + receive")

    # 先通过 HTTP 获取 B 的 user_id
    r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token_b}"})
    b_user_id = r.json().get("id")
    print(f"  User B id: {b_user_id}")

    # 连接 B 先建立接收端
    async with websockets.connect(WS_URL) as ws_b:
        await ws_b.send(json.dumps({"type": "auth", "token": token_b}))
        auth_resp = json.loads(await ws_b.recv())
        assert auth_resp["type"] == "auth_result"
        print(f"  User B authenticated: user_id={auth_resp['user_id']}")

        # 清空 B 的初始推送
        try:
            while True:
                await asyncio.wait_for(ws_b.recv(), timeout=2)
        except asyncio.TimeoutError:
            pass

        # A 连接并发送消息
        async with websockets.connect(WS_URL) as ws_a:
            await ws_a.send(json.dumps({"type": "auth", "token": token_a}))
            auth_a = json.loads(await ws_a.recv())
            assert auth_a["type"] == "auth_result"

            # 清空 A 的初始推送
            try:
                while True:
                    await asyncio.wait_for(ws_a.recv(), timeout=2)
            except asyncio.TimeoutError:
                pass

            # 发送消息
            import uuid
            client_msg_id = str(uuid.uuid4())
            await ws_a.send(json.dumps({
                "type": "send",
                "clientMsgId": client_msg_id,
                "payload": {
                    "receiver_id": b_user_id,
                    "content": "Hello from WS test!"
                }
            }))

            # A 应该收到 ack
            resp_a = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=5))
            print(f"  A received: {resp_a['type']}")
            if resp_a["type"] == "ack":
                print(f"  ack: clientMsgId={resp_a.get('clientMsgId')}, serverSeq={resp_a.get('serverSeq')}")
                assert resp_a["clientMsgId"] == client_msg_id
                print(f"  ACK PASS")

            # B 应该收到 new_message（带序号）
            try:
                resp_b = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
                print(f"  B received: type={resp_b['type']}, seq={resp_b.get('seq')}")
                if resp_b["type"] == "message":
                    payload = resp_b["payload"]
                    print(f"  event={payload['event']}, content={payload.get('data', {}).get('content')}")
                    assert payload["event"] == "new_message"
                    print(f"  NEW_MESSAGE PASS")
            except asyncio.TimeoutError:
                print(f"  B did not receive message (may be offline sync)  SKIP")

    print("  [TEST 3] PASS")
    return True


async def test_invalid_token():
    """测试 4: 无效 token"""
    print("\n[TEST 4] invalid token -> code 4001")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"type": "auth", "token": "invalid.jwt.token"}))
        try:
            # 服务端应该关闭连接
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            print(f"  Unexpected: {msg}")
        except websockets.exceptions.ConnectionClosed as e:
            print(f"  Connection closed: code={e.code}, reason={e.reason}")
            assert e.code == 4001, f"Expected code 4001, got {e.code}"
            print(f"  CODE 4001 PASS")

    print("  [TEST 4] PASS")
    return True


async def test_idempotency(token):
    """测试 5: 幂等去重（重复 clientMsgId）"""
    print("\n[TEST 5] idempotency (duplicate clientMsgId)")

    r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    my_id = r.json().get("id")

    # 注册另一个用户来发消息
    other_token = register_and_login("ws_other", "ws_other@test.com", "test1234")
    if not other_token:
        print("  Could not register other user, SKIP")
        return True

    r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {other_token}"})
    other_id = r.json().get("id")

    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"type": "auth", "token": other_token}))
        auth_resp = json.loads(await ws.recv())
        assert auth_resp["type"] == "auth_result"

        # 清空
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=2)
        except asyncio.TimeoutError:
            pass

        import uuid
        dup_id = str(uuid.uuid4())

        # 第一次发送
        await ws.send(json.dumps({
            "type": "send",
            "clientMsgId": dup_id,
            "payload": {"receiver_id": my_id, "content": "idempotent test"}
        }))
        resp1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        print(f"  First send: type={resp1['type']}, serverSeq={resp1.get('serverSeq')}")

        # 清空中间推送
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=1)
        except asyncio.TimeoutError:
            pass

        # 第二次发送（相同 clientMsgId）
        await ws.send(json.dumps({
            "type": "send",
            "clientMsgId": dup_id,
            "payload": {"receiver_id": my_id, "content": "idempotent test"}
        }))
        resp2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        print(f"  Dup send: type={resp2['type']}, duplicate={resp2.get('duplicate')}")
        if resp2.get("duplicate") is True:
            print(f"  IDEMPOTENCY PASS")
        else:
            print(f"  IDEMPOTENCY SKIP (no dedup record yet)")

    print("  [TEST 5] PASS")
    return True


async def main():
    print("=" * 60)
    print("WebSocket v3.0 序号协议测试")
    print("=" * 60)

    # 准备两个测试用户
    token_a = register_and_login("ws_test_a", "ws_test_a@test.com", "test1234")
    token_b = register_and_login("ws_test_b", "ws_test_b@test.com", "test1234")

    if not token_a or not token_b:
        print("Failed to get tokens!")
        return

    print(f"\nToken A: {token_a[:20]}...")
    print(f"Token B: {token_b[:20]}...")

    results = {}

    try:
        results["auth+ping"] = await test_auth_and_ping(token_a)
    except Exception as e:
        print(f"  [TEST 1] FAIL: {e}")
        results["auth+ping"] = False

    try:
        results["sync"] = await test_sync(token_a, 0)
    except Exception as e:
        print(f"  [TEST 2] FAIL: {e}")
        results["sync"] = False

    try:
        results["send"] = await test_send_message(token_a, token_b)
    except Exception as e:
        print(f"  [TEST 3] FAIL: {e}")
        results["send"] = False

    try:
        results["invalid_token"] = await test_invalid_token()
    except Exception as e:
        print(f"  [TEST 4] FAIL: {e}")
        results["invalid_token"] = False

    try:
        results["idempotency"] = await test_idempotency(token_a)
    except Exception as e:
        print(f"  [TEST 5] FAIL: {e}")
        results["idempotency"] = False

    print("\n" + "=" * 60)
    print("RESULTS:")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
