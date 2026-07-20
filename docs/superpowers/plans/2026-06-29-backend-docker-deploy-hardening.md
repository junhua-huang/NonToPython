# Backend Docker Deploy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the FastAPI backend Docker deployment safer and more reliable for Baota/Alibaba Cloud without changing business behavior.

**Architecture:** Keep the existing FastAPI entrypoint, Docker image layout, and direct Docker run workflow. Make minimal changes to dependency declaration, Docker build context exclusions, debug configuration, and mirror selection.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Docker, Alembic, python-dotenv.

---

## File Structure

- Modify `D:\NanTuPy\requirements.txt`: add the missing `python-dotenv` runtime dependency used by `app/main.py` and `alembic/env.py`.
- Modify `D:\NanTuPy\.dockerignore`: add `.env` so local secrets are not copied into the Docker image by `COPY . .`.
- Modify `D:\NanTuPy\app\core\config.py`: change `Config.DEBUG` from a hardcoded `True` to an environment-driven boolean with safe default `False`.
- Modify `D:\NanTuPy\Dockerfile`: replace Tsinghua mirrors with Alibaba Cloud mirrors for Debian apt and Python pip installs.

## Task 1: Add missing dotenv dependency

**Files:**
- Modify: `D:\NanTuPy\requirements.txt`

- [ ] **Step 1: Confirm current dependency list**

Check that `python-dotenv` is not already present:

```bash
python - <<'PY'
from pathlib import Path
text = Path(r'D:\NanTuPy\requirements.txt').read_text(encoding='utf-8')
print('python-dotenv' in text)
PY
```

Expected output:

```text
False
```

- [ ] **Step 2: Add `python-dotenv>=1.0.0`**

Append this line to `D:\NanTuPy\requirements.txt`:

```txt
python-dotenv>=1.0.0
```

The final dependency file should include:

```txt
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy>=2.0.50
pymysql==1.1.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.12
python-socketio==5.10.0
Pillow>=10.2.0
cos-python-sdk-v5
pydantic>=2.0.0
pydantic-settings>=2.0.0
werkzeug>=3.0.0
markupsafe>=2.1.0
httpx>=0.27.0
aiosmtplib>=3.0.0
alembic>=1.13.0
python-dotenv>=1.0.0
```

- [ ] **Step 3: Verify the dependency was added once**

Run:

```bash
python - <<'PY'
from pathlib import Path
lines = Path(r'D:\NanTuPy\requirements.txt').read_text(encoding='utf-8').splitlines()
matches = [line for line in lines if line.strip().startswith('python-dotenv')]
print(matches)
assert matches == ['python-dotenv>=1.0.0']
PY
```

Expected output:

```text
['python-dotenv>=1.0.0']
```

## Task 2: Prevent `.env` from entering Docker image

**Files:**
- Modify: `D:\NanTuPy\.dockerignore`

- [ ] **Step 1: Confirm `.env` is not ignored yet**

Run:

```bash
python - <<'PY'
from pathlib import Path
lines = Path(r'D:\NanTuPy\.dockerignore').read_text(encoding='utf-8').splitlines()
print('.env' in [line.strip() for line in lines])
PY
```

Expected output:

```text
False
```

- [ ] **Step 2: Add `.env` near other local/environment exclusions**

Edit `D:\NanTuPy\.dockerignore` so the top section becomes:

```dockerignore
__pycache__
*.pyc
.env
.venv
venv
.git
.idea
.qoder
.vscode
.DS_Store
```

- [ ] **Step 3: Verify `.env` is ignored once**

Run:

```bash
python - <<'PY'
from pathlib import Path
lines = [line.strip() for line in Path(r'D:\NanTuPy\.dockerignore').read_text(encoding='utf-8').splitlines()]
matches = [line for line in lines if line == '.env']
print(matches)
assert matches == ['.env']
PY
```

Expected output:

```text
['.env']
```

## Task 3: Make debug mode environment-driven

**Files:**
- Modify: `D:\NanTuPy\app\core\config.py`

- [ ] **Step 1: Confirm current unsafe debug default**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path(r'D:\NanTuPy\app\core\config.py').read_text(encoding='utf-8')
print('    DEBUG = True' in text)
PY
```

Expected output:

```text
True
```

- [ ] **Step 2: Replace hardcoded debug setting**

Replace this line in `D:\NanTuPy\app\core\config.py`:

```python
    DEBUG = True
```

with:

```python
    DEBUG = os.environ.get('DEBUG', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
```

- [ ] **Step 3: Verify debug values resolve correctly**

Run:

```bash
cd /d/NanTuPy && python - <<'PY'
import importlib
import os
import app.core.config as config_module

os.environ.pop('DEBUG', None)
importlib.reload(config_module)
print(config_module.Config.DEBUG)
assert config_module.Config.DEBUG is False

os.environ['DEBUG'] = 'true'
importlib.reload(config_module)
print(config_module.Config.DEBUG)
assert config_module.Config.DEBUG is True

os.environ['DEBUG'] = 'false'
importlib.reload(config_module)
print(config_module.Config.DEBUG)
assert config_module.Config.DEBUG is False
PY
```

Expected output:

```text
False
True
False
```

## Task 4: Switch Docker mirrors to Alibaba Cloud

**Files:**
- Modify: `D:\NanTuPy\Dockerfile`

- [ ] **Step 1: Confirm current mirror strings**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path(r'D:\NanTuPy\Dockerfile').read_text(encoding='utf-8')
print('mirrors.tuna.tsinghua.edu.cn' in text)
print('pypi.tuna.tsinghua.edu.cn' in text)
PY
```

Expected output:

```text
True
True
```

- [ ] **Step 2: Replace Dockerfile mirror configuration**

Change the install block in `D:\NanTuPy\Dockerfile` from:

```dockerfile
# 换清华源 + 安装系统依赖
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
```

to:

```dockerfile
# 换阿里云源 + 安装系统依赖
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
```

Change the pip install line from:

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

to:

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

- [ ] **Step 3: Verify Dockerfile no longer uses Tsinghua mirrors**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path(r'D:\NanTuPy\Dockerfile').read_text(encoding='utf-8')
print('mirrors.tuna.tsinghua.edu.cn' in text)
print('pypi.tuna.tsinghua.edu.cn' in text)
print('mirrors.aliyun.com' in text)
assert 'mirrors.tuna.tsinghua.edu.cn' not in text
assert 'pypi.tuna.tsinghua.edu.cn' not in text
assert 'mirrors.aliyun.com' in text
PY
```

Expected output:

```text
False
False
True
```

## Task 5: Final verification and deployment commands

**Files:**
- Verify: `D:\NanTuPy\requirements.txt`
- Verify: `D:\NanTuPy\.dockerignore`
- Verify: `D:\NanTuPy\app\core\config.py`
- Verify: `D:\NanTuPy\Dockerfile`

- [ ] **Step 1: Run local static verification**

Run:

```bash
python - <<'PY'
from pathlib import Path

root = Path(r'D:\NanTuPy')
requirements = (root / 'requirements.txt').read_text(encoding='utf-8')
dockerignore = (root / '.dockerignore').read_text(encoding='utf-8')
config = (root / 'app' / 'core' / 'config.py').read_text(encoding='utf-8')
dockerfile = (root / 'Dockerfile').read_text(encoding='utf-8')

assert 'python-dotenv>=1.0.0' in requirements
assert '.env' in [line.strip() for line in dockerignore.splitlines()]
assert "DEBUG = os.environ.get('DEBUG', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}" in config
assert 'mirrors.aliyun.com' in dockerfile
assert 'tuna.tsinghua' not in dockerfile
print('static verification passed')
PY
```

Expected output:

```text
static verification passed
```

- [ ] **Step 2: Use these server commands after uploading changed files**

Run on the Baota/Alibaba Cloud server from the backend project directory:

```bash
cd /www/wwwroot/NonToPyDev
docker build -t nonto-backend .
docker run --rm --env-file .env nonto-backend alembic upgrade head
docker stop nonto-backend 2>/dev/null; docker rm nonto-backend 2>/dev/null
docker run -d --name nonto-backend -p 5000:5000 --restart unless-stopped --env-file .env nonto-backend
docker logs -f nonto-backend
```

Expected result:

- Docker build completes without apt mirror timeout.
- Alembic migration completes or reports the database is already at head.
- `docker run` prints a container id.
- `docker logs -f nonto-backend` shows Uvicorn started on `0.0.0.0:5000`.

- [ ] **Step 3: Health-check the running API on the server**

Run on the server:

```bash
curl http://127.0.0.1:5000/health
```

Expected output is a JSON health response from the FastAPI app.
