"""Quick WS log test - triggers connect/auth/disconnect to verify logging"""
import os
import pytest

if os.getenv("NONTO_RUN_LIVE_WS_TESTS") != "1":
    pytest.skip(
        "live WebSocket diagnostic test requires NONTO_RUN_LIVE_WS_TESTS=1 and a running local server",
        allow_module_level=True,
    )

import asyncio, json, requests, websockets

# Login
r = requests.post("http://localhost:5000/api/auth/login", json={"login":"ws_test_a@test.com","password":"test1234"})
token = r.json()["access_token"]

async def test():
    async with websockets.connect("ws://localhost:5000/ws") as ws:
        # auth
        await ws.send(json.dumps({"type":"auth","token":token}))
        resp = json.loads(await ws.recv())
        print(f"auth_result: {resp}")
        # drain session_list
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                print(f"  auto-push: type={msg.get('type')} seq={msg.get('seq','N/A')}")
        except: pass
        # ping
        await ws.send(json.dumps({"type":"ping"}))
        pong = json.loads(await ws.recv())
        print(f"pong: {pong}")
        # sync
        await ws.send(json.dumps({"type":"sync","lastReceivedSeq":0}))
        sync = json.loads(await ws.recv())
        print(f"sync_result: count={sync.get('count')}")
    print("disconnected")

asyncio.run(test())
