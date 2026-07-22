# Sofia AI Emergent – OpenClaw Hosting Platform

> A production-grade web application that provisions, proxies, and authenticates
> an [OpenClaw](https://github.com/openclaw/moltbot) (Moltbot) LLM gateway for a
> single authenticated user.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start (Docker)](#quick-start-docker)
3. [Local Development (No Docker)](#local-development-no-docker)
4. [Environment Variables](#environment-variables)
5. [API Reference](#api-reference)
6. [Testing Strategy](#testing-strategy)
7. [CI/CD Pipeline](#cicd-pipeline)
8. [Deployment](#deployment)
9. [Security Baseline](#security-baseline)
10. [Operational Runbook](#operational-runbook)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                      Browser                         │
└─────────────────────┬────────────────────────────────┘
                      │ HTTPS
┌─────────────────────▼────────────────────────────────┐
│  nginx (frontend)   :3000 / :80                      │
│  React SPA          /api/* → backend proxy           │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP + WS
┌─────────────────────▼────────────────────────────────┐
│  FastAPI backend    :8001                            │
│  ├─ Auth  (/api/auth/*)                              │
│  ├─ OpenClaw gateway management (/api/openclaw/*)    │
│  ├─ HTTP + WS proxy to local OpenClaw (:18789)       │
│  ├─ Security headers middleware                      │
│  ├─ Rate limiting (slowapi)                          │
│  └─ Health check  /api/health                        │
└────────────┬────────────────┬────────────────────────┘
             │                │
  ┌──────────▼───┐     ┌──────▼──────────────┐
  │   MongoDB    │     │ OpenClaw gateway     │
  │   :27017     │     │ (supervisord) :18789 │
  └──────────────┘     └──────────────────────┘
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Single-user lock | The instance is locked to the first authenticated user who starts the gateway. |
| Supervisor-managed gateway | The OpenClaw gateway outlives backend restarts; supervisor handles auto-restart. |
| HTTP-only cookies | `httponly=True` prevents XSS token theft. `secure=True` + `samesite=none` for cross-origin production. |
| In-memory rate limiter | Simple and zero-dependency for single-process; swap for Redis via `slowapi` config for multi-process. |

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/enricorizz/sofia-ai-emergent.git
cd sofia-ai-emergent

# 2. Configure
cp .env.example .env
# Edit .env – at minimum, MONGO_URL is handled by docker compose automatically

# 3. Build & run
docker compose up --build

# Frontend → http://localhost:3000
# Backend  → http://localhost:8001/api/health
```

---

## Local Development (No Docker)

### Prerequisites

- Python 3.11+
- Node 20 + Yarn 1.x
- MongoDB 7 (local or Atlas)
- supervisord (for the OpenClaw gateway; optional for API-only dev)

### Backend

```bash
cd backend

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example .env        # then edit .env

uvicorn server:app --reload --port 8001
```

### Frontend

```bash
cd frontend
yarn install
yarn start          # http://localhost:3000
```

---

## Environment Variables

Copy `.env.example` to `backend/.env` (or export at runtime).

| Variable | Required | Default | Description |
|---|---|---|---|
| `MONGO_URL` | ✅ | — | MongoDB connection string |
| `DB_NAME` | | `moltbot_app` | Database name |
| `CORS_ORIGINS` | | `http://localhost:3000` | Comma-separated allowed origins |
| `EMERGENT_API_KEY` | | — | Emergent platform API key |
| `EMERGENT_BASE_URL` | | `https://integrations.emergentagent.com/llm` | Emergent proxy base URL |
| `ENVIRONMENT` | | `development` | Set `production` to enable HSTS |
| `LOG_LEVEL` | | `INFO` | Python log level (DEBUG/INFO/WARNING/ERROR) |

> ⚠️ Never commit `.env` files. The `.gitignore` already excludes `*.env`.

---

## API Reference

All endpoints are prefixed with `/api`.

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness + DB readiness probe |

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/auth/instance` | None | Check if the instance is locked |
| `POST` | `/auth/session` | None | Exchange Emergent session_id for cookie (10 req/min) |
| `GET` | `/auth/me` | Cookie/****** Get current user |
| `POST` | `/auth/logout` | Cookie/****** Delete session + clear cookie |

### OpenClaw Gateway

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/openclaw/status` | Optional | Gateway running state |
| `POST` | `/openclaw/start` | Required | Start gateway (5 req/min) |
| `POST` | `/openclaw/stop` | Required (owner) | Stop the gateway |
| `GET` | `/openclaw/token` | Required (owner) | Retrieve gateway auth token |
| `GET/POST/…` | `/openclaw/ui/{path}` | Required (owner) | HTTP proxy to OpenClaw UI |
| `WS` | `/openclaw/ws` | — | WebSocket proxy to OpenClaw |
| `GET` | `/openclaw/whatsapp/status` | None | WhatsApp link status |

### Legacy

| Method | Path | Description |
|---|---|---|
| `POST` | `/status` | Create a status check entry |
| `GET` | `/status` | List status check entries |

---

## Testing Strategy

### Unit tests (pytest + httpx, no real DB needed)

```bash
pytest tests/ -v
```

All MongoDB and external calls are mocked with `unittest.mock.AsyncMock`.
Tests cover: auth endpoints, health check, OpenClaw validation, security headers.

### Integration / smoke tests (requires running stack)

```bash
BACKEND_URL=http://localhost:8001/api python backend/backend_test.py
```

### Linting

```bash
# From repo root
black --check backend/
isort --check-only backend/
flake8 backend/
```

---

## CI/CD Pipeline

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it does |
|---|---|
| `backend-lint` | `black`, `isort`, `flake8` |
| `backend-test` | `pytest tests/ -v` |
| `frontend-lint` | `eslint src/` |
| `frontend-build` | `yarn build` + artifact upload |

---

## Deployment

### One-command deploy (Docker Compose)

```bash
# On the server:
git pull origin main
docker compose build
docker compose up -d --remove-orphans
```

### Rolling update

```bash
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
```

### Rollback

```bash
# Tag before deploying
docker tag sofia-backend:latest sofia-backend:prev

# On rollback
docker tag sofia-backend:prev sofia-backend:latest
docker compose up -d --no-deps backend
```

---

## Security Baseline

| Control | Implementation |
|---|---|
| Security headers | `SecurityHeadersMiddleware` – X-Frame-Options DENY, X-Content-Type-Options nosniff, X-XSS-Protection, Referrer-Policy, Permissions-Policy, HSTS in production |
| CORS | Explicit allowlist via `CORS_ORIGINS` (no wildcard in production) |
| Rate limiting | `slowapi` – 10 req/min on auth session, 5 req/min on gateway start |
| Session TTL | 7-day expiry, MongoDB TTL index auto-purges expired docs |
| HttpOnly cookies | Prevents XSS token exfiltration |
| Input validation | Pydantic v2 on all request bodies |
| Instance ownership | Single-user lock; non-owners receive 403 |
| Request correlation | `X-Request-ID` header on every response |
| No secrets in repo | `.env` excluded from git; only `.env.example` committed |

---

## Operational Runbook

### Health check

```bash
curl http://localhost:8001/api/health
# → {"status":"ok","db":"connected","timestamp":"..."}
```

### View logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

### MongoDB maintenance

```bash
docker compose exec mongo mongosh moltbot_app
db.user_sessions.getIndexes()   # verify TTL index exists
db.users.getIndexes()           # verify unique email index
```

### Restart gateway (without restarting backend)

```bash
docker compose exec backend supervisorctl restart openclaw-gateway
```

### Full stack restart

```bash
docker compose restart
```

### Multi-replica note

> The in-memory rate limiter is per-process. For multi-replica deployments,
> configure a Redis backend:
>
> ```python
> limiter = Limiter(key_func=get_remote_address,
>                   storage_uri=os.environ["REDIS_URL"])
> ```
