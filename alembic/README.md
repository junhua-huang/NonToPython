# Alembic 数据库迁移

本项目使用 [Alembic](https://alembic.sqlalchemy.org/) 管理 MySQL 表结构。
**应用启动时不会自动迁移**，所有迁移由开发者手动执行。

## 常用命令

所有命令在项目根 `D:\NanTuPy` 下、用 `.venv` 的 Python 执行：

```cmd
:: 1. 改完模型后，生成迁移脚本（会对比模型 vs 数据库当前状态）
D:\NanTuPy\.venv\Scripts\python.exe -m alembic revision -m "描述本次变更" --autogenerate

:: 2. 审查 alembic/versions/ 下新生成的 .py（重点看 upgrade() 里的 DDL 是否符合预期）

:: 3. 应用迁移到数据库
D:\NanTuPy\.venv\Scripts\python.exe -m alembic upgrade head

:: 查看当前版本
D:\NanTuPy\.venv\Scripts\python.exe -m alembic current

:: 查看迁移历史
D:\NanTuPy\.venv\Scripts\python.exe -m alembic history

:: 回退一个版本
D:\NanTuPy\.venv\Scripts\python.exe -m alembic downgrade -1

:: 新环境/全新数据库部署
D:\NanTuPy\.venv\Scripts\python.exe -m alembic upgrade head
```

## 工作流

1. **修改 `app/models/models.py`**（加列/改类型/加表等）
2. `alembic revision -m "xxx" --autogenerate` 生成迁移
3. **打开生成的迁移文件审查** —— autogenerate 不是万能的，尤其注意：
   - MySQL 的 `server_default`、索引名、外键约束名常被误判为"差异"
   - 类型变更（如 `JSON` vs `Text`）要确认是否真的要改
   - 删表/删列操作尤其危险，确认无误再执行
4. `alembic upgrade head` 应用
5. 提交 `models.py` + 生成的迁移文件到版本库

## 配置说明

- `alembic.ini`：`sqlalchemy.url` 留空，连接串由 `alembic/env.py` 从 `.env`
  读取（复用 `app.core.config.Config.SQLALCHEMY_DATABASE_URI`），避免硬编码密码
- `alembic/env.py`：启用了 `compare_type=True`（检测列类型变更）和
  `compare_server_default=True`（检测默认值变更）；忽略 `alembic_version` 表
- 迁移文件名带日期前缀（`alembic.ini` 的 `file_template`）

## baseline 说明

`2026_06_19_1703-1fd93272c5db_baseline.py` 是基线迁移，**不执行任何 DDL**
（upgrade/downgrade 均为 pass）。它通过 `alembic stamp head` 把现有数据库
标记为迁移链的零点。

autogenerate 在生成基线时检测到大量历史差异（遗留表 `comic_event`/`shares`、
漫展表单复数命名 `comic_event` vs `comic_events`、索引/外键/默认值差异、
`posts.images` 的 `JSON` vs `Text` 类型差异等），**均未在基线中处理**。
这些历史债应在各自独立的后续迁移中逐项解决，而不是塞进 baseline。

## 注意事项

- ❌ 不要再用 `Base.metadata.create_all()` 建表（`init_db()` 已改为空壳）
- ❌ 不要手动 ALTER 表 —— 一律走 alembic 迁移
- ✅ 迁移文件一旦提交上线，**不要修改**，只能新增
- ✅ 远程 MySQL（sqlpub）有完整 DDL 权限，可正常 ALTER
