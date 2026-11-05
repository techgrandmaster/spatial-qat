
# Spatial-QAT

Interactive, multimodal STEM and hardware troubleshooting tutor that transforms static technical datasheets and circuit board photographs into spatially grounded learning and active-recall workflows.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              VITE + REACT 19 FRONTEND (apps/web)             │
│  SVG Mask Overlay  •  Push-to-Talk  •  SSE Agent Trace      │
│  src/api/client.ts (Bearer auth + error envelope)           │
└───────────────────┬─────────────────────────┬───────────────┘
                    │ HTTPS JSON              │ SSE
                    ▼                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  FASTAPI ORCHESTRATION (backend/)            │
│  PDF Ingestion  •  Spatial Discovery  •  Agent Loop (7 tools)│
│  Grading Engine •  SHA-256 Cache   •  Budget Guard          │
└───────────────────┬─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│              PostgreSQL 16 (db/schema.sql)                   │
│  JSONB • Generated GIN FTS • UUID PKs • Normalized [0,1]    │
│  Agent-run trace rows back multi-worker SSE streams          │
└─────────────────────────────────────────────────────────────┘
```

All workspace-scoped API routes require `Authorization: Bearer <guest-token>`
(FR-01). Tokens are issued by `POST /api/v1/sessions`, SHA-256 hashed at rest,
and expire after 7 days. Missing workspaces yield 404; workspaces owned by
another session yield 403. Error responses use the canonical
`{error: {code, message}}` envelope.

## Repository Layout

```
spatial-qat/
├── apps/
│   └── web/                        # Vite + React 19 + TypeScript + Tailwind
│       ├── src/
│       │   ├── components/         # BoardOverlay, InspectorPanel, SpatialQuizHUD, etc.
│       │   └── api/
│       │       ├── types.ts        # TypeScript interfaces synced with schemas.py
│       │       └── client.ts       # Base-URL + Bearer + error-envelope fetch client
│       ├── Dockerfile              # Prod build (VITE_API_URL baked in)
│       ├── Dockerfile.dev          # Hot-reload dev server
│       └── package.json
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── deps.py             # get_session_id + load_owned_workspace (FR-01)
│   │   │   └── v1/                 # sessions, workspaces, assets, components (+edges),
│   │   │                           # qa (+illustrations, voice-stream), assessments, runs
│   │   ├── agent/                  # 8-step loop, budgeted tool fan-out, run_analyze_pipeline
│   │   │                           # (real bbox/area/mask PNG, SoM grounding wired)
│   │   ├── adapters/meta/          # http.py (shared client), sam/spark/voice/image.py
│   │   ├── services/               # pdf_ingest, som_tagger, grading, budget_guard
│   │   ├── models/                 # schemas.py (Pydantic), orm.py (SQLAlchemy)
│   │   ├── config.py               # pydantic-settings (incl. audio cap, public base URL)
│   │   └── main.py                 # App factory, lifespan, canonical error handlers
│   ├── alembic/                    # Database migrations
│   ├── eval/
│   │   ├── run_evals.py            # Ablation harness (pipelines A/B/C)
│   │   └── measure_slo.py          # SLO + eval-scale probe (python -m eval.measure_slo)
│   ├── scripts/seed_demo.py        # Demo seeding (already sends Bearer headers)
│   ├── tests/unit/                 # test_grading.py, test_authz.py (34 tests)
│   └── pyproject.toml
├── data/
│   ├── boards/                     # Curated board photos
│   ├── datasheets/                 # Official PDF datasheets
│   ├── labels.jsonl                # Gold labels & benchmark Q&A
│   └── LICENSES.md
├── db/
│   └── schema.sql                  # Authoritative PostgreSQL 16 DDL
├── docs/
│   ├── PRD.md                      # Product requirements (FR-01..FR-13)
│   ├── EVALUATION_REPORT.md        # Ablation & latency metrics
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
# Edit .env: set MODEL_API_KEY and PUBLIC_BASE_URL (public backend origin —
# Meta's model API fetches board images via absolute asset URLs)

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

Frontend dev server: http://localhost:3000 (Vite proxies `/api` to the
backend). Leave `VITE_API_URL` empty for same-origin; set it only to point the
web build at a remote backend.

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed deployment options, environment variables, and troubleshooting.

## Tech Stack

- **Frontend:** React 19 + Vite + TypeScript + Tailwind CSS
- **Backend:** Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 (Async)
- **Spatial:** Shapely (prepared geometries) + Pillow (mask PNGs, EXIF handling)
- **Documents:** PyMuPDF (page-indexed PDF extraction, off event loop)
- **Database:** PostgreSQL 16 (JSONB, generated GIN full-text search)
- **Models:** Meta Model API (muse-spark-1.3, sam-3.1, muse-voice-transcribe-1.0, muse-image-1.0)

## Performance Notes (refactored hot paths)

- Grading parses each polygon once per attempt (prepared geometries), computes
  near-miss in pixel/diagonal units per PRD §10A, and decodes mask PNGs in a
  worker thread. Measured: p50 ~0.3ms, max ~0.6ms over 200 attempts (<150ms SLO).
- PDF ingest and the analyze pipeline flush once per stage instead of once per
  row; `submit_attempt` resolves target/clicked labels from a single component
  fetch; the `SUM(cost_usd)` budget aggregate carries a 5s TTL cache.
- One shared `httpx.AsyncClient` serves all model adapters; cache keys stream
  message hashes (no full 1M-context JSON dump); `record_cost` closes the
  duplicate-key race with a savepoint.
- Uploads stream in 1MiB chunks with early 413s and per-kind caps (image/PDF/
  audio); images are EXIF-normalized and re-encoded before storage; PDF page
  counts and image decodes run off the event loop.

## Evaluation

```bash
cd backend
uv run python -m eval.measure_slo   # SLO probe: latency, eval scale, harness gaps
uv run python -m eval.run_evals     # Ablation harness (pipelines A/B/C)
uv run pytest                       # 34 unit tests (grading + ownership)
uv run ruff check app/ tests/ eval/ # Lint gate
```

Current scale: 19 gold masks + 3 QAs in `data/labels.jsonl` vs the PRD target
of 30–40 masks + 30 Qs; live-API SLOs (cached Q&A, cold p50/p95, recall/naming/
grounding, spend) need a live backend and the full gold set.

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

- [PRD.md](docs/PRD.md) — Product requirements (FR-01..FR-13)
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Production deployment guide, demo video script, and troubleshooting
- [EVALUATION_REPORT.md](docs/EVALUATION_REPORT.md) — Ablation & latency metrics
- [LIMITATIONS.md](docs/LIMITATIONS.md) — Known failure modes
- [SUBMISSION_CHECKLIST.md](docs/SUBMISSION_CHECKLIST.md) — Hackathon submission checklist

## License

TBD
