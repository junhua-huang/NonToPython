# NanTuPy 宝塔 Docker 部署说明

本包用于在宝塔服务器上通过 Docker Compose 构建并运行 NanTuPy FastAPI 后端。

## 1. 上传并解压

把 `NanTuPy-docker-deploy.zip` 上传到服务器，例如：

```bash
/www/wwwroot/NanTuPy
```

解压后进入目录：

```bash
cd /www/wwwroot/NanTuPy
```

## 2. 配置环境变量

复制模板：

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
vim .env
```

至少需要填写：

```env
SECRET_KEY=...
JWT_SECRET_KEY=...
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=...
DB_PASS=...
DB_NAME=...
ALIYUN_ACCESS_KEY_ID=...
ALIYUN_ACCESS_KEY_SECRET=...
ALIYUN_PUSH_APP_KEY_ANDROID=...
```

如果 MySQL 运行在宝塔宿主机，通常保留：

```env
DB_HOST=host.docker.internal
```

`docker-compose.yml` 已配置：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

如果数据库在另一台服务器，改成实际内网或公网地址。

## 3. 构建镜像

```bash
docker compose build
```

老版本 Docker 如果没有 `docker compose`，使用：

```bash
docker-compose build
```

## 4. 启动服务

```bash
docker compose up -d
```

启动时容器会自动执行：

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

上传文件目录会挂载到宿主机：

```text
./uploads:/app/app/uploads
```

这样容器重建后上传文件不会丢失。

## 5. 查看状态和日志

```bash
docker compose ps
docker compose logs -f nantupy-backend
```

本机测试：

```bash
curl http://127.0.0.1:5000/docs
```

如果返回 HTML，说明服务已启动。

## 6. 宝塔 Nginx 反向代理

在宝塔网站设置里添加反向代理：

```text
目标 URL: http://127.0.0.1:5000
```

如果使用 WebSocket，需要确保 Nginx 带有升级头：

```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

## 7. 常见问题

### 7.1 数据库连接失败

检查 `.env`：

```env
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=...
DB_PASS=...
DB_NAME=...
```

检查宝塔 MySQL 是否允许该用户从 Docker 网关连接。必要时给数据库用户授权宿主机网段访问。

### 7.2 迁移失败

查看日志：

```bash
docker compose logs nantupy-backend
```

容器启动脚本会先执行 `alembic upgrade head`。迁移失败时容器不会继续启动 API。

### 7.3 阿里云移动推送不生效

检查 `.env`：

```env
ALIYUN_ACCESS_KEY_ID=...
ALIYUN_ACCESS_KEY_SECRET=...
ALIYUN_PUSH_APP_KEY_ANDROID=...
ALIYUN_PUSH_ANDROID_ACTIVITY=com.nonto.nonto.MainActivity
ALIYUN_PUSH_ANDROID_CHANNEL_ID=nonto_message_alerts
```

RAM 用户需要具备移动推送权限，例如 `AliyunMPushFullAccess`。

## 8. 更新部署

上传新包后：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```
