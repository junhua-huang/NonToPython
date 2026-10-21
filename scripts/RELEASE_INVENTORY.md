# 南图发布配置快照

在与现有后端相同的 Python、环境变量和工作目录下运行：

```bash
python scripts/release_inventory.py --env-file /www/wwwroot/NonToPyDev/NanTuPy/.env --database
```

脚本依赖现有 requirements 中的 python-dotenv、SQLAlchemy 和 MySQL 驱动。继承的环境变量优先于指定 .env，与后端 load_dotenv 默认行为一致。

仅读取白名单配置，并可选执行一条 `admin_settings` 的 SELECT；不会读取用户记录、启动应用、建表、迁移、写入、删除、发送邮件或调用外部存储服务。只输出主机类别、配置地域、SMTP 主机、服务是否配置及图片审核开关，不输出密码、私钥、账号邮箱、数据库 URL、桶名、对象地址或文件路径。无需把 .env 发给任何人。

不带 `--database` 或数据库查询失败时，最终图片审核状态是 null，不能把环境开关当作最终结果；查询失败返回非零状态且不输出含连接凭据的异常。数据库覆盖优先于环境设置。

这是启动配置快照，不是线上进程、真实审核、删除或法律合规认证。默认广州地域和默认 QQ SMTP 主机仍需要和实际使用及合同对应；本机数据库也不说明物理地域。存储生命周期、备份保留、注销清理、真实身份与未成年人安排，以及邮件实际收信响应需要独立证据。脚本不会改动 iOS 的任何验收标记。
