
# Spatial-QAT

Interactive, multimodal STEM and hardware troubleshooting tutor that transforms static technical datasheets and circuit board photographs into spatially grounded learning and active-recall workflows.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              NEXT.JS + TYPESCRIPT FRONTEND (apps/web)        │
│  SVG Mask Overlay  •  Push-to-Talk  •  SSE Agent Trace      │
└───────────────────┬─────────────────────────┬───────────────┘
                    │ HTTPS JSON              │ SSE
                    ▼                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  FASTAPI ORCHESTRATION (backend/)            │
│  PDF Ingestion  •  Spatial Discovery  •  Agent Loop         │
│  Grading Engine •  SHA-256 Cache   •  Budget Guard          │
└───────────────────┬─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│              PostgreSQL 16 (db/schema.sql)                   │
│  JSONB • GIN FTS • UUID PKs • Normalized [0,1] coords       │
└─────────────────────────────────────────────────────────────┘
```

## Repository Layout

```
spatial-qat/
├── apps/
│   └── web/                        # Vite + React 19 + TypeScript + Tailwind
│       ├── src/
│       │   ├── components/         # BoardOverlay, InspectorPanel, SpatialQuizHUD, etc.
│       │   └── api/types.ts        # TypeScript interfaces synced with schemas.py
│       └── package.json
├── backend/
│   ├── app/
│   │   ├── api/v1/                 # FastAPI routers
│   │   ├── agent/                  # Hand-written 8-step loop + tools
│   │   ├── adapters/meta/          # sam.py, spark.py, voice.py, image.py
│   │   ├── services/               # pdf_ingest, som_tagger, grading, budget_guard
│   │   ├── models/                 # schemas.py (Pydantic), orm.py (SQLAlchemy)
│   │   ├── config.py               # pydantic-settings
│   │   └── main.py                 # FastAPI app factory
│   ├── alembic/                    # Database migrations
│   ├── eval/                       # CLI evaluation & ablation harness
│   ├── tests/                      # unit/, contract/, e2e/
│   └── pyproject.toml
├── data/
│   ├── boards/                     # Curated board photos
│   ├── datasheets/                 # Official PDF datasheets
│   ├── labels.jsonl                # Gold labels & benchmark Q&A
│   └── LICENSES.md
├── db/
│   └── schema.sql                  # Authoritative PostgreSQL 16 DDL
├── docs/
│   ├── EVALUATION_REPORT.md        # Auto-generated metrics
│   ├── LIMITATIONS.md              # Known failure modes
│   ├── DEPLOYMENT.md               # Production deployment guide
│   └── SUBMISSION_CHECKLIST.md     # Hackathon submission checklist
├── docker-compose.yml              # Production: one-command startup
├── docker-compose.dev.yml          # Development overrides (hot-reload)
├── .env.example
└── README.md
```

## Quick Start

### Production Deployment

```bash
# 1. Copy environment variables
cp .env.example .env
# Edit .env and set your MODEL_API_KEY

# 2. Start all services
docker compose up --build -d

# 3. Seed the demo board (pre-warms SHA-256 cache)
cd backend && uv run python scripts/seed_demo.py

# 4. Open the app
# Frontend: http://localhost
# Backend API: http://localhost:8000/docs
```

### Development Mode (hot-reload)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed deployment options, environment variables, and troubleshooting.

## Tech Stack

- **Frontend:** React 19 + Vite + TypeScript + Tailwind CSS
- **Backend:** Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 (Async)
- **Spatial:** Shapely + Pillow (binary mask hit-testing)
- **Documents:** PyMuPDF (page-indexed PDF extraction)
- **Database:** PostgreSQL 16 (JSONB, GIN full-text search)
- **Models:** Meta Model API (muse-spark-1.3, sam-3.1, muse-voice-transcribe-1.0)

## Database Setup

### Using Docker (recommended)

The `docker-compose.yml` includes a PostgreSQL 16 service. The database is initialized automatically on first run.

### Local PostgreSQL

If you prefer a local PostgreSQL installation:

```bash
# 1. Create a PostgreSQL role for your OS user (run once)
sudo -u postgres createuser --login "$USER"

# 2. Create the database
createdb spatial_qat

# 3. Load the schema (idempotent — safe to re-run)
psql -d spatial_qat -f db/schema.sql
```

> **Note:** If peer authentication fails (`FATAL: Peer authentication failed`), either use the
> `sudo -u postgres createuser` step above, or connect via TCP with password auth:
> `psql -U postgres -h 127.0.0.1 -d spatial_qat -f db/schema.sql`.

## Development

```bash
# Backend
cd backend
uv sync
uv run uvicorn app.main:app --reload

# Frontend
cd apps/web
npm install
npm run dev

# Tests
cd backend
uv run pytest

# Linting
cd backend
uv run ruff check .
```

## Documentation

- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Production deployment guide, demo video script, and troubleshooting
- [EVALUATION_REPORT.md](docs/EVALUATION_REPORT.md) — Ablation & latency metrics
- [LIMITATIONS.md](docs/LIMITATIONS.md) — Known failure modes
- [SUBMISSION_CHECKLIST.md](docs/SUBMISSION_CHECKLIST.md) — Hackathon submission checklist

## License

TBD