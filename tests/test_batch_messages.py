"""
批量消息接口测试脚本

用法:
  pip install requests      # 如未安装
  python tests/test_batch_messages.py

前置条件:
  1. 服务运行在 http://127.0.0.1:5000
  2. 修改下方的 TEST_USER 和 TEST_CONV_IDS
"""
import sys
import time
import json

# ============ 配置：替换为实际测试数据 ============
TEST_USER = {
    "username": "testuser",
    "password": "testpass",
}
TEST_CONV_IDS = "1,2,3"
PER_PAGE = 30
# ================================================

BASE = "http://127.0.0.1:5000/api"


def check_deps():
    try:
        import requests
        return requests
    except ImportError:
        print("ERROR: 缺少 requests 库，请先安装:")
        print("  pip install requests")
        sys.exit(1)


def login(requests):
    resp = requests.post(
        f"{BASE}/auth/login",
        json={"username": TEST_USER["username"], "password": TEST_USER["password"]},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def run_test(requests):
    print("登录中...")
    try:
        token = login(requests)
    except Exception as e:
        print(f"登录失败: {e}")
        print("请修改 TEST_USER 为有效的登录凭据")
        sys.exit(1)
    print("Token 获取成功\n")

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{BASE}/chat/messages/batch"
    params = {"conv_ids": TEST_CONV_IDS, "per_page": PER_PAGE}
    conv_id_list = [x.strip() for x in TEST_CONV_IDS.split(",") if x.strip()]

    # ===== 批量接口测试 =====
    print("=" * 60)
    print(f"测试: GET {url}?conv_ids={TEST_CONV_IDS}&per_page={PER_PAGE}")
    print("=" * 60)

    # 预热
    try:
        requests.get(url, headers=headers, params=params, timeout=10)
    except Exception as e:
        print(f"预热失败: {e}")
        return

    times_ms = []
    for i in range(10):
        start = time.perf_counter()
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        times_ms.append(elapsed)

        if i == 0:
            data = resp.json()
            convs = data.get("data", {}).get("conversations", [])
            print(f"状态码: {resp.status_code}")
            print(f"会话数: {len(convs)}")
            for c in convs:
                print(f"  会话 {c['conversation_id']}: {len(c['messages'])} 条消息")
            print(f"响应大小: {len(resp.content):,} bytes ({len(resp.content)/1024:.1f} KB)")
            print()

    avg = sum(times_ms) / len(times_ms)
    sorted_times = sorted(times_ms)

    print("=" * 60)
    print("批量接口性能（10次）")
    print("=" * 60)
    print(f"  平均: {avg:.1f} ms")
    print(f"  P50:  {sorted_times[5]:.1f} ms")
    print(f"  P99:  {sorted_times[9]:.1f} ms")
    print(f"  最小: {sorted_times[0]:.1f} ms")
    print(f"  最大: {sorted_times[-1]:.1f} ms")
    print()
    print(f"  DB 查询: 2 次（1验证 + 1 UNION ALL）")
    print(f"  子查询数: {len(conv_id_list)}（各带 LIMIT {PER_PAGE}）")
    print(f"  扫描行数: <= {len(conv_id_list) * PER_PAGE}")

    # ===== 对比：逐个请求 =====
    print()
    print("=" * 60)
    print(f"对比: 逐个 GET /chat/messages/{{id}}（取前 3 个会话）")
    print("=" * 60)
    compare_times = []
    for i in range(3):
        start = time.perf_counter()
        for cid in conv_id_list[:3]:
            requests.get(
                f"{BASE}/chat/messages/{cid}",
                headers=headers,
                params={"limit": PER_PAGE},
                timeout=10,
            )
        compare_times.append((time.perf_counter() - start) * 1000)
    compare_avg = sum(compare_times) / len(compare_times)
    print(f"  逐会话 3 个平均: {compare_avg:.1f} ms")
    save_pct = (compare_avg - avg) / compare_avg * 100 if compare_avg > 0 else 0
    print(f"  批量接口节省:    ~{save_pct:.0f}% 延迟")
    print()


if __name__ == "__main__":
    requests = check_deps()
    try:
        run_test(requests)
    except requests.exceptions.ConnectionError:
        print("ERROR: 无法连接 http://127.0.0.1:5000，请确保服务已启动")
    except requests.exceptions.HTTPError as e:
        print(f"HTTP 错误: {e}")
    except Exception as e:
        print(f"错误: {e}")