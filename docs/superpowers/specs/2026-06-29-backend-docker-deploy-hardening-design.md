# Backend Docker Deploy Hardening Design

## Context

The backend is a FastAPI application started by Docker with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

The current deployment target is a Baota/Alibaba Cloud server using direct Docker commands. The goal is to make the existing deployment build and run reliably without changing API behavior or database schema.

## Goals

1. Make the Docker image start successfully by ensuring runtime dependencies are declared.
2. Prevent local secrets from being copied into the Docker image.
3. Make production debug behavior safe by default.
4. Reduce Debian/PyPI package download failures on Alibaba Cloud by using Alibaba Cloud mirrors.

## Non-goals

- No business route changes.
- No database model changes.
- No Alembic migration changes.
- No conversion to Docker Compose.
- No production configuration architecture rewrite.

## Changes

### `requirements.txt`

Add `python-dotenv>=1.0.0` because both `app/main.py` and `alembic/env.py` import `dotenv.load_dotenv`.

### `.dockerignore`

Add `.env` so sensitive local configuration is not copied by `COPY . .` during image build.

### `app/core/config.py`

Change `DEBUG = True` to read from the `DEBUG` environment variable, defaulting to `false`. This prevents OTP codes from being logged in production when email sending fails.

Expected behavior:

```env
DEBUG=false
```

keeps debug behavior disabled.

```env
DEBUG=true
```

enables local development debug behavior.

### `Dockerfile`

Replace Tsinghua Debian and PyPI mirrors with Alibaba Cloud mirrors to better match the Alibaba Cloud deployment environment.

The application command, exposed port, and Python base image remain unchanged.

## Deployment expectation

After changes, the server should run the container with an environment file instead of baking `.env` into the image:

```bash
docker run -d --name nonto-backend -p 5000:5000 --restart unless-stopped --env-file .env nonto-backend
```

The `.env` file must exist on the server in the deployment directory and should include production settings such as:

```env
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
HIDE_API_DOCS=1
```

## Verification

Minimum local verification:

1. Confirm edited files contain expected changes.
2. Optionally run dependency import check if local Python environment is available.

Recommended server verification after upload:

```bash
docker build -t nonto-backend .
docker run --rm --env-file .env nonto-backend alembic upgrade head
docker stop nonto-backend 2>/dev/null; docker rm nonto-backend 2>/dev/null
docker run -d --name nonto-backend -p 5000:5000 --restart unless-stopped --env-file .env nonto-backend
docker logs -f nonto-backend
curl http://127.0.0.1:5000/health
```
