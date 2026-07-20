# Baota Docker Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Baota-friendly Docker deployment bundle for the NanTuPy FastAPI backend that can be uploaded, extracted, configured, built, migrated, and run on the server.

**Architecture:** Package the backend as source plus Docker metadata. The container runs only FastAPI/uvicorn and connects to an external MySQL instance configured through `.env`; startup runs Alembic migrations before starting the API. Secrets are excluded from the bundle and represented by `.env.example` only.

**Tech Stack:** FastAPI, Uvicorn, SQLAlchemy, Alembic, PyMySQL, Docker, Docker Compose, Bash entrypoint.

---

## File Structure

- Modify `Dockerfile`: keep Python 3.12 slim, install runtime build dependencies, copy source, expose 5000, use entrypoint.
- Modify `.dockerignore`: exclude local secrets, logs, venv, generated packages, tests/docs as appropriate while keeping deployment files.
- Create `docker-compose.yml`: build local image, map port 5000, load `.env`, add `host.docker.internal` host-gateway for Baota MySQL on host.
- Create `deploy/entrypoint.sh`: run `alembic upgrade head`, then exec uvicorn.
- Create `.env.example`: production env key list without real secrets.
- Create `deploy/README_BAOTA_DOCKER.md`: upload, configure, build, run, logs, migration, Nginx reverse proxy notes.
- Create `NanTuPy-docker-deploy.zip` on Desktop: contains deployment source and excludes `.env`.

## Task 1: Docker runtime entrypoint

**Files:**
- Create: `deploy/entrypoint.sh`

- [ ] Write entrypoint that exits on migration failure and starts uvicorn on 0.0.0.0:5000.
- [ ] Ensure line endings are LF-compatible for Linux containers.
- [ ] Verify script content does not print environment secrets.

## Task 2: Dockerfile update

**Files:**
- Modify: `Dockerfile`

- [ ] Install required Debian packages for Python deps and MySQL client libs.
- [ ] Install Python requirements from `requirements.txt`.
- [ ] Copy app, alembic config, deployment scripts.
- [ ] Mark entrypoint executable.
- [ ] Use `/app/deploy/entrypoint.sh` as CMD.

## Task 3: Compose file

**Files:**
- Create: `docker-compose.yml`

- [ ] Define service `nantupy-backend`.
- [ ] Build from current directory.
- [ ] Map `5000:5000`.
- [ ] Load `.env`.
- [ ] Add `host.docker.internal:host-gateway` for Linux Docker connecting to host MySQL.
- [ ] Set restart policy `unless-stopped`.

## Task 4: Environment template

**Files:**
- Create: `.env.example`

- [ ] Include non-secret placeholders for DB, JWT, CORS, COS, SMTP, Aliyun Mobile Push.
- [ ] Do not copy local `.env` values.
- [ ] Document `DB_HOST=host.docker.internal` default for Baota host MySQL.

## Task 5: Baota README

**Files:**
- Create: `deploy/README_BAOTA_DOCKER.md`

- [ ] Explain upload and unzip.
- [ ] Explain copying `.env.example` to `.env` and filling secrets.
- [ ] Explain Docker Compose commands.
- [ ] Explain checking logs and API docs.
- [ ] Explain Baota Nginx reverse proxy to `127.0.0.1:5000`.
- [ ] Explain common MySQL connection issues.

## Task 6: Verification

**Commands:**
- `docker compose config`
- `docker build -t nantupy-backend:baota .`

- [ ] Verify compose config succeeds.
- [ ] Verify Docker image builds locally.
- [ ] Do not run a container against production `.env` unless explicitly needed.

## Task 7: Deployment zip

**Files:**
- Create: `C:/Users/25318/Desktop/NanTuPy-docker-deploy.zip`

- [ ] Zip backend source and deployment files.
- [ ] Exclude `.env`, logs, cache, venv, generated tar/zip files, tests cache.
- [ ] Verify zip exists and list top-level entries.
