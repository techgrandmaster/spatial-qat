# Spatial-QAT Deployment Guide

## Quick Start

### Production Deployment

```bash
# 1. Copy environment variables
cp .env.example .env
# Edit .env with your MODEL_API_KEY

# 2. Build and start all services
docker compose up --build -d

# 3. Seed the demo board (pre-warms cache)
cd backend && uv run python scripts/seed_demo.py

# 4. Access the application
# Frontend: http://localhost
# Backend API: http://localhost:8000
# Health check: http://localhost:8000/api/v1/health
```

### Development Mode

```bash
# With hot-reload for both frontend and backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Nginx (port)  │────▶│   FastAPI        │
│   Frontend:80   │     │   Backend:8000   │
│                 │     │                  │
│  - SPA serving  │     │  - REST API      │
│  - API proxy    │     │  - SSE streams   │
│  - Gzip         │     │  - Agent loop    │
└─────────────────┘     └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │   PostgreSQL 16   │
                        │   (port 5432)     │
                        │                   │
                        │  - GIN FTS index  │
                        │  - JSONB storage  │
                        │  - UUID PKs       │
                        └──────────────────┘
```

## Pre-Warming the Demo Board

The `seed_demo.py` script creates a sample workspace with:
1. Guest session with 7-day expiry
2. Arduino Uno R3 demo workspace
3. Board image upload (from `data/boards/`)
4. Datasheet PDF upload (from `data/datasheets/`)
5. Full board analysis (warms SHA-256 cache)
6. Assessment quiz generation

After seeding, the demo board works entirely from cache — no live API calls needed for repeat visits.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MODEL_API_KEY` | Yes | — | Meta Model API key |
| `DATABASE_URL` | No | `postgresql+asyncpg://postgres:postgres@postgres:5432/spatial_qat` | Database connection |
| `BUDGET_USD_HARD_CAP` | No | `120.0` | Maximum API spend cap |
| `MAX_SAM_CONCURRENCY` | No | `4` | Parallel SAM segmentation limit |
| `AGENT_MAX_STEPS` | No | `8` | Agent loop step ceiling |
| `AGENT_TIMEOUT_SECONDS` | No | `90` | Agent loop wall-clock timeout |

## Demo Video Script (90–120 seconds)

### Hook (0–15s)
- Show the board photo with component masks lighting up
- "This AI tutor turns static circuit board photos into interactive learning experiences"

### Upload & Analysis (15–35s)
- Upload Arduino Uno photo + datasheet PDF
- Watch the agent trace panel as components are discovered
- Highlight the SHA-256 cache badge

### Interactive Inspection (35–55s)
- Hover over components to see labels
- Click a component to open the inspector panel
- Show datasheet citations linking to specific pages

### Voice Q&A (55–75s)
- Push-to-talk: "What does this chip do?"
- Show relevant masks lighting up on the board
- Response reads aloud via speech synthesis

### Spatial Quiz (75–105s)
- Start a quiz with hidden labels
- Click to locate a component
- Show spatial + conceptual grading feedback
- "Near miss — you're 2% away from the target"

### Closing (105–120s)
- Show the agent trace panel with cost/latency metrics
- "Spatial-QAT: bridging visual perception and active recall"

## Deployment Options

### Option 1: Docker Compose (Recommended for Hackathon)
```bash
docker compose up --build -d
```

### Option 2: Cloud Deployment
1. Push images to container registry
2. Deploy PostgreSQL (managed service or container)
3. Deploy backend (FastAPI on any PaaS)
4. Deploy frontend (static hosting with API proxy)
5. Set environment variables
6. Run seed script

### Option 3: Single-Server Deployment
```bash
# On a VPS with Docker
git clone https://github.com/techgrandmaster/spatial-qat
cd spatial-qat
cp .env.example .env
# Edit .env
docker compose up --build -d
```

## Health Checks

```bash
# Backend health
curl http://localhost:8000/api/v1/health

# Database connectivity
docker compose exec postgres pg_isready -U postgres

# Frontend
curl -I http://localhost
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Backend won't start | Check `.env` has valid `MODEL_API_KEY` |
| Database connection failed | Ensure PostgreSQL is healthy: `docker compose ps postgres` |
| Frontend can't reach API | Verify Nginx proxy config points to `backend:8000` |
| Analysis fails | Check budget hasn't exceeded cap: `SELECT SUM(cost_usd) FROM model_calls` |
| SAM segmentation slow | Reduce `MAX_SAM_CONCURRENCY` or check API rate limits |
