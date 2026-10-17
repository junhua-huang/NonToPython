# 原生 iOS APNs 服务端

本改动位于 `ios` 分支，对接 [NonToFlutter/ios](https://github.com/junhua-huang/NonToFlutter/tree/ios)。业务私信和通知使用现有 NotificationService 触发；Android Aliyun 通道保留，iOS 直接通过 HTTP/2 接入 APNs，不引入移动端推送 SDK。

## 必需配置

在运行服务的服务器环境配置：

```dotenv
APNS_KEY_ID=你的苹果推送KeyID
APNS_TEAM_ID=你的AppleDeveloperTeamID
APNS_BUNDLE_ID=online.nonto.native
APNS_PRIVATE_KEY_PATH=/run/nonto-apns/AuthKey.p8
```

这是 APNs 推送密钥，不能用 App Store Connect API Key 替代。私钥、设备 token 和安装撤销凭据不要放进 Git、客户端或日志。

Docker 部署可使用 `deploy/docker-compose.apns.yml`：将 `.p8` 保存在仓库目录外的服务器目录中，文件命名 `AuthKey.p8`，只给后端进程读取权限。在 `.env` 加入 `APNS_KEY_DIR=/绝对路径/苹果推送密钥目录` 以及上述 Key ID / Team ID / Bundle ID。

```sh
git fetch origin ios
git switch ios
docker compose -f docker-compose.yml -f deploy/docker-compose.apns.yml up -d --build
```

现有 entrypoint 会先执行 `alembic upgrade head`，其中新增 `2026_10_18_0100` 迁移。非 Docker 部署先安装 `requirements.txt`，执行该迁移，再重新启动现有服务。修改分支、数据库或生产服务前按自己的发布流程备份并检查迁移。

## 接口

- `POST /api/push/devices/register`：鉴权；`provider=apns`、`platform=ios`、稳定安装 `device_id=apns:<UUID>`、`apns_token`、`apns_environment=development|production`、`apns_topic`、`binding_secret`，可附 app_state 和版本。Topic 必须与服务器允许的 Bundle ID 一致。同一安装更换用户需要匹配安装撤销凭据。
- `POST /api/push/devices/revoke`：使用安装 ID + 256 位随机撤销凭据解绑，不依赖可能已失效的账号 access token。服务端仅保存凭据 SHA-256；接口不透露绑定用户或存在状态。客户端串行化注册与撤销，撤销失败时持久化待处理标记并在再次联网时重试。
- `POST /api/push/devices/state`：复用前后台状态接口。APNs 发送不依赖该状态来抑制消息，因为崩溃/被系统终止可能没有后台回调。
- `POST /api/push/devices/test`：鉴权，只能测试自己已启用的 APNs 设备；创建真实通知并经过同样的可见性和发送链路；60 秒频率限制。`submitted=true` 表示 APNs 接受请求，不保证用户已经看到横幅。
- `GET /api/notifications/{id}`：鉴权并检查当前可见性，供通知冷启动跳转读取内容。

环境由设备实际签名决定：development 发往 `api.sandbox.push.apple.com`，production 发往 `api.push.apple.com`。JWT 使用 ES256 签名并复用 50 分钟；HTTP/2 连接复用。苹果返回失效/不匹配 token 时禁用对应 token；迟到的旧 token 响应不会禁用已轮换的新 token。

推送复用现有逐通知/逐设备数据库 claim 去重、屏蔽与通知可见性检查、notify_push/notify_message 偏好；notify_sound 控制 APNs 声音。锁屏载荷仅包含通用提示和路由 ID，不包含私聊正文。

## 测试

```sh
python -m pytest tests/test_apns_push.py tests/test_aliyun_push_contracts.py tests/test_app_state_push_contracts.py tests/test_push_dedupe_contracts.py tests/test_notification_service_unit.py tests/test_presence_app_state_contracts.py -q
```

以上使用 SQLite、临时测试密钥和模拟 APNs HTTP 响应，不连接真实苹果推送、不修改生产数据库。真实投递仍需维护者配置苹果开发者后台、签名与服务器私钥后按客户端 `ios/APNS.md` 真机验收。
